"""Frozen-prediction audit; never claims to train or establish walk-forward.

Both models must be evaluated on identical, timestamp-proven fixtures.  This
gate is necessary for, but insufficient to establish, a production backtest:
the provenance of each model's training inputs must also be independently
audited before calling an experiment walk-forward.
"""
from __future__ import annotations

from .snapshot import _datetime, _THREE, SCHEMA_VERSION, build_snapshot


def _identity(row: dict) -> tuple[str, str]:
    period, match_id = row.get("period"), row.get("match_id")
    if not isinstance(period, str) or not isinstance(match_id, str) or not period or not match_id:
        raise ValueError("缺少期号或赛事ID")
    return period, match_id


def _index(rows: list[dict], name: str) -> dict[tuple[str, str], dict]:
    out = {}
    for row in rows:
        key = _identity(row)
        if key in out:
            raise ValueError(f"{name} 重复赛事 {key}")
        out[key] = row
    return out


def _forecast(row: dict, cutoff: str, kickoff: str) -> dict:
    if row.get("schema_version") != SCHEMA_VERSION or row.get("status") != "shadow":
        raise ValueError("不接受非 BD-1 影子快照")
    if (row.get("observation_only") is not False
            or row.get("as_of_backtest_eligible") is not True
            or row.get("provenance_unverified") is not False):
        raise ValueError("预测没有可核验赛前来源")
    ko = _datetime(kickoff, "kickoff_at")
    if _datetime(row.get("kickoff_at"), "kickoff_at") != ko:
        raise ValueError("预测快照与赛果开球时间不一致")
    decision = _datetime(cutoff, "decision_cutoff_at")
    asof = _datetime(row.get("asof_at"), "asof_at")
    generated = _datetime(row.get("generated_at"), "generated_at")
    if not asof <= generated <= decision < ko:
        raise ValueError("预测并非在统一赛前决策时点可用")
    sources = row.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("缺少来源采集证据")
    for source in sources:
        if (not isinstance(source, dict) or source.get("status") != "ok"
                or _datetime(source.get("available_at"), "available_at") > asof):
            raise ValueError("来源缺失或赛前不可用")
    # Rebuild to recheck all six complete vectors and their shared score basis;
    # an imported JSON file's eligibility booleans are not proof by themselves.
    rebuilt = build_snapshot(
        match_id=row["match_id"], period=row["period"], kickoff_at=row["kickoff_at"],
        asof_at=row["asof_at"], generated_at=row["generated_at"],
        model_version=row.get("model_version"), vectors=row.get("vectors"),
        sources=sources, lambda_home=row.get("lambda_home"),
        lambda_away=row.get("lambda_away"), handicap=row.get("handicap"),
        input_sources=row.get("input_sources"),
        competition_family=row.get("competition_family"),
        synthetic_sample=row.get("synthetic_sample", False),
        training_lineage=row.get("training_lineage"),
        identity_ids=row.get("identity_ids"),
        identity_provenance=row.get("identity_provenance"),
        market_provenance=row.get("market_provenance"),
    )
    if rebuilt["as_of_backtest_eligible"] is not True:
        raise ValueError("来源未核验")
    return rebuilt["vectors"]["wdl"]


def _brier(vector: dict[str, float], ft_home: int, ft_away: int) -> float:
    outcome = "胜" if ft_home > ft_away else ("平" if ft_home == ft_away else "负")
    return sum((vector[k] - (k == outcome)) ** 2 for k in _THREE) / 3


def audit_frozen_pair(*, baseline: list[dict], candidate: list[dict],
                      fixtures: list[dict], evaluated_at: str) -> dict:
    """Check equal coverage and compare three-class Brier on settled full pool.

    ``fixtures`` lists the entire offered pool and each row has period,
    match_id, kickoff_at, decision_cutoff_at, ft_home, ft_away, and a
    documented result_available_at or first-seen verified_at. Skips stay in
    the coverage denominator. This scores *saved* predictions, not refits.
    """
    base = _index(baseline, "基线预测")
    challenger = _index(candidate, "候选预测")
    pool = _index(fixtures, "赛程")
    if not pool or set(base) != set(challenger) or not set(base) <= set(pool):
        raise ValueError("基线与候选必须在同一完整赛程上成对计分")
    now = _datetime(evaluated_at, "evaluated_at")
    scores = []
    for key, result in pool.items():
        if key not in base:
            continue
        if _datetime(base[key].get("asof_at"), "asof_at") != _datetime(
            challenger[key].get("asof_at"), "asof_at"
        ):
            raise ValueError("成对预测的赛前 as-of 时点不一致")
        ko = _datetime(result.get("kickoff_at"), "kickoff_at")
        observed = [_datetime(result[k], k) for k in ("result_available_at", "verified_at")
                    if result.get(k) is not None]
        if not observed or any(t <= ko for t in observed) or max(observed) > now:
            raise ValueError("赛果尚未有可信确认时刻")
        ft_h, ft_a = result.get("ft_home"), result.get("ft_away")
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (ft_h, ft_a)):
            raise ValueError("常规时间比分非法")
        cutoff = result.get("decision_cutoff_at")
        a = _forecast(base[key], cutoff, result.get("kickoff_at"))
        b = _forecast(challenger[key], cutoff, result.get("kickoff_at"))
        scores.append((_brier(a, ft_h, ft_a), _brier(b, ft_h, ft_a)))
    if not scores:
        raise ValueError("没有可成对计分的赛事")
    brier_base = sum(a for a, _ in scores) / len(scores)
    brier_candidate = sum(b for _, b in scores) / len(scores)
    return {
        "label": "paired_asof_audit_not_walk_forward",
        "offered_n": len(pool), "paired_n": len(scores),
        "coverage": len(scores) / len(pool),
        "brier_baseline": brier_base,
        "brier_candidate": brier_candidate,
        "delta_brier": brier_candidate - brier_base,
        "nondegradation_on_paired_set": brier_candidate <= brier_base + 1e-12,
        "production_gate_passed": False,  # training chronology/holdout still unproven
    }
