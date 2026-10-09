"""Actual rolling refits and past-only candidate selection, research only.

Execution time is always current and is never backdated to a historical cutoff.
Self-reported source metadata is not independent proof of archive chronology.
This executor does not grant production approval or call the shared engine.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random

from .baseline import _history, predict_l3, _vectors_from_matrix
from .evaluation import _brier
from .history_import import load_verified_history
from .mean_preserving import project_fixed_mean, ProjectionUnavailable, OUTCOMES
from .snapshot import _datetime


# Declared candidates, never selected by the current/future test outcomes.
KAPPAS = (None, 25., 50., 100.)  # None is exact identity, the safe cold start.
RIDGES = (None, 2., 5., 10., 20.)  # None retains L3 until earlier results select L1.


def _team_candidate(prior, train, fixture, ridge, cutoff, min_history, identity_mode):
    if ridge is None:
        return {"vectors": prior["vectors"], "score_31": prior["score_31"],
                "status": "identity", "mean_drift": 0.}
    # Team/stage features have their own archive clock; a kickoff source alone
    # must not silently approve team IDs or present-day season assignments.
    _fixture_source({"fixture_source": fixture.get("identity_source")}, _datetime(cutoff, "cutoff"))
    if identity_mode == "provider_native_espn":
        from .provider_native import predict_espn_native
        fitted = predict_espn_native(train, asof_at=cutoff, kickoff_at=fixture["kickoff_at"],
            home_id=fixture["provider_home_id"], away_id=fixture["provider_away_id"],
            league_id=fixture["provider_league_id"], season_year=fixture["season_year"],
            season_type=fixture["season_type"], handicap=prior["handicap"],
            ridge=ridge, min_history=min_history)
    else:
        from .team_strength import predict_l1
        if fixture.get("identity_verified") is not True:
            raise ValueError("规范身份未批准")
        fitted = predict_l1(train, asof_at=cutoff, kickoff_at=fixture["kickoff_at"],
            home_id=fixture["home_id"], away_id=fixture["away_id"],
            competition_id=fixture["competition_id"], handicap=prior["handicap"],
            ridge=ridge, min_history=min_history)
    mean = lambda score: math.fsum(sum(map(int, k.split("-"))) * p for k,p in score.items())
    return {"vectors": fitted["vectors"], "score_31": fitted["score_31"],
            "status": "team_strength_research", "ridge": ridge,
            "training_ids": fitted["training_match_ids"], "training_n": fitted["training_n"],
            "mean_drift": mean(fitted["vectors"]["score"]) - mean(prior["vectors"]["score"]),
            "mean_change_is_estimated_team_effect_not_score_inflation": True}


def summarize_goal_means(pairs):
    """Require absolute empirical bias nondegradation, with block uncertainty.

    This assesses new team-strength means; fixed-mean calibration has a separate
    numerical constraint. Never borrow the shared engine's -0.008 as our bias.
    """
    keys = ("goal_error_baseline", "goal_error_candidate")
    complete = bool(pairs) and all(all(isinstance(r.get(k), (int,float))
                  and not isinstance(r[k],bool) and math.isfinite(r[k]) for k in keys) for r in pairs)
    if not complete:
        return {"complete":False,"mean_nondegradation_gate":False,"baseline_bias":None,"candidate_bias":None}
    b,c = (math.fsum(r[k] for r in pairs)/len(pairs) for k in keys)
    groups = {}
    for r in pairs: groups.setdefault(r["period"],[]).append(r)
    upper = lo = hi = None
    if len(groups)>=5:
        rng=random.Random(20261009);blocks=list(groups.values());deltas=[];biases=[]
        for _ in range(2000):
            drawn=[r for _ in blocks for r in rng.choice(blocks)]
            bb,cc=(math.fsum(r[k] for r in drawn)/len(drawn) for k in keys)
            deltas.append(abs(cc)-abs(bb));biases.append(cc)
        upper=sorted(deltas)[math.ceil(.95*(len(deltas)-1))]
        biases.sort();lo=biases[math.floor(.025*(len(biases)-1))];hi=biases[math.ceil(.975*(len(biases)-1))]
    sample_ok=len(groups)>=5 and len(pairs)>=500
    return {"complete":True,"baseline_bias":b,"candidate_bias":c,
        "absolute_bias_delta":abs(c)-abs(b),"absolute_bias_delta_bootstrap_upper_95":upper,
        "candidate_bias_block_interval_95":[lo,hi],"sample_threshold_met":sample_ok,
        "mean_nondegradation_gate":bool(sample_ok and abs(c)<=abs(b)+1e-9 and upper is not None
                                          and upper<=1e-9 and lo<=0<=hi),
        "statistical_unbiasedness_proved":False}


def _observed_result(row: dict) -> datetime:
    ko = _datetime(row.get("kickoff_at"), "kickoff_at")
    stamps = [_datetime(row[k], k) for k in ("result_available_at", "verified_at") if row.get(k) is not None]
    if (row.get("regular_time") is not True or not stamps or any(t <= ko for t in stamps)
            or any(isinstance(row.get(k), bool) or not isinstance(row.get(k), int)
                   or row[k] < 0 for k in ("ft_home", "ft_away"))):
        raise ValueError("赛果或真实可得时间无效")
    return max(stamps)


def _fixture_source(row: dict, cutoff: datetime) -> None:
    source = row.get("fixture_source")
    if (not isinstance(source, dict) or source.get("status") != "ok"
            or not isinstance(source.get("name"), str) or not source["name"]
            or source.get("available_at") is None):
        raise ValueError("赛程原始来源或真实可得时间缺失")
    if _datetime(source["available_at"], "fixture_source.available_at") > cutoff:
        raise ValueError("赛程在决策时点尚不可得")


def _candidate(prior: dict, rows: list[dict], kappa: float | None) -> dict:
    if kappa is None:
        return {"vectors": prior["vectors"], "score_31": prior["score_31"],
                "status": "identity", "mean_drift": 0.}
    counts = Counter("胜" if r["ft_home"] > r["ft_away"] else "平" if r["ft_home"] == r["ft_away"] else "负"
                     for r in rows)
    p = prior["vectors"]["wdl"]
    # Global frequencies shrink towards the current fitted Poisson WDL.
    # No assumed 29% draw rate, no unaudited SP, no team-name identity join.
    q = {k: (counts[k] + kappa * p[k]) / (len(rows) + kappa) for k in OUTCOMES}
    try:
        fit = project_fixed_mean(prior["vectors"]["score"], target=q, line=0)
    except ProjectionUnavailable as exc:
        return {"vectors": prior["vectors"], "score_31": prior["score_31"],
                "status": "fallback_prior", "reason": str(exc), "mean_drift": 0.}
    n = max(max(map(int, s.split("-"))) for s in fit["score"]) + 1
    matrix = [[fit["score"].get(f"{h}-{a}", 0.) for a in range(n)] for h in range(n)]
    f = prior["ht_fractions"]
    vectors, score31 = _vectors_from_matrix(matrix, f["home"], f["away"], prior["handicap"])
    return {"vectors": vectors, "score_31": score31, "status": "projected",
            "mean_drift": fit["mean_after"] - fit["mean_before"]}


def summarize_pairs(pairs: list[dict], *, football_offered_n: int,
                    predicted_n: int) -> dict:
    """Predeclared numerical gate; never certifies data or approves release."""
    groups = {}
    for row in pairs:
        if any(not isinstance(row[k], (int, float)) or isinstance(row[k], bool)
               or not math.isfinite(row[k]) or not 0 <= row[k] <= 2 / 3 + 1e-12
               for k in ("baseline", "candidate")):
            raise ValueError("成对Brier值非法")
        groups.setdefault(row["period"], []).append(row["candidate"] - row["baseline"])
    point = math.fsum(math.fsum(g) for g in groups.values()) / len(pairs) if pairs else None
    upper = None
    # Below five settled blocks, no degenerate interval masquerades as evidence.
    if len(groups) >= 5:
        rng = random.Random(20261009)
        blocks = list(groups.values())
        boot = []
        for _ in range(2000):
            drawn = [rng.choice(blocks) for _ in blocks]
            boot.append(math.fsum(math.fsum(g) for g in drawn) / sum(map(len, drawn)))
        upper = sorted(boot)[math.ceil(.95 * (len(boot) - 1))]
    sample_ok = len(groups) >= 5 and len(pairs) >= 500
    full = football_offered_n > 0 and predicted_n == len(pairs) == football_offered_n
    score_complete = bool(pairs) and all(all(isinstance(r.get(k), (int, float))
                        and not isinstance(r[k], bool) and math.isfinite(r[k]) and r[k] >= 0
                        for k in ("score31_logloss_baseline", "score31_logloss_candidate")) for r in pairs)
    score_delta = (math.fsum(r["score31_logloss_candidate"] - r["score31_logloss_baseline"]
                            for r in pairs) / len(pairs)) if score_complete else None
    support_ok = bool(pairs) and all(r.get("floored_probabilities") == 0 for r in pairs)
    return {"settled_period_n": len(groups), "min_settled_periods": 5, "min_pairs": 500,
            "sample_threshold_met": sample_ok, "full_settled_football_coverage": full,
            "paired_delta_brier": point, "block_bootstrap_upper_95": upper,
            "paired_delta_score31_logloss": score_delta,
            "no_logloss_floor_events": support_ok,
            "bootstrap_replicates": 2000 if upper is not None else 0,
            "numerical_nondegradation_gate": bool(sample_ok and full and point <= 1e-12
                                                   and upper is not None and upper <= 1e-12
                                                   and score_delta is not None and score_delta <= 1e-12
                                                   and support_ok),
            "production_gate_passed": False}


def run_walk_forward(*, history: list[dict], folds: list[dict],
                     results: list[dict], evaluated_at: str,
                     min_history: int = 30, min_selection_matches: int = 30,
                     model_family: str = "l3_calibration", identity_mode: str = "canonical") -> dict:
    """Refit at each supplied cutoff; choose kappa from earlier settled folds.

    Only unhandicapped calibration is implemented here. Missing handicap stays
    null. The full supplied pool is retained in the ledger, including skips.
    Outcomes enter selection only after both their fixture's fold and their
    documented result availability. Test outcomes never enter their own fit.
    """
    if not isinstance(folds, list) or not folds:
        raise ValueError("没有滚动测试池")
    if model_family not in ("l3_calibration", "l1_team_strength") or identity_mode not in ("canonical", "provider_native_espn"):
        raise ValueError("未知模型或身份模式")
    grid = KAPPAS if model_family == "l3_calibration" else RIDGES
    for value in (min_history, min_selection_matches):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("最低样本量必须为正整数")
    now = _datetime(evaluated_at, "evaluated_at")
    hi = {r["match_id"]: r for r in history}
    ri = {r["match_id"]: r for r in results}
    if len(hi) != len(history) or len(ri) != len(results):
        raise ValueError("历史或赛果赛事ID重复")
    result_times = {i: _observed_result(r) for i, r in ri.items()}
    # Same ID cannot carry contradictory labels or an earlier availability
    # clock in one channel. Use the latest actual documented clock in both.
    for identity in set(hi) & set(ri):
        a, b = hi[identity], ri[identity]
        if (any(a.get(k) != b.get(k) for k in ("ft_home", "ft_away", "regular_time"))
                or _datetime(a["kickoff_at"], "history.kickoff_at")
                != _datetime(b["kickoff_at"], "result.kickoff_at")):
            raise ValueError("历史与计分赛果在同一ID下不一致")
        for key in ("ht_home","ht_away","provider_match_id","provider_home_id","provider_away_id",
                    "provider_league_id","season_year","season_type","home_id","away_id","competition_id"):
            if a.get(key) is not None and b.get(key) is not None and a[key]!=b[key]:
                raise ValueError("历史与计分赛果的半场、身份或赛事阶段矛盾")
        latest = max(_observed_result(a), result_times[identity])
        hi[identity] = {**a, "verified_at": latest.isoformat()}
        result_times[identity] = latest
    training_history = list(hi.values())
    # Validate the complete roster before computing anything.
    seen, previous = set(), None
    for fold in folds:
        cutoff = _datetime(fold.get("cutoff_at"), "cutoff_at")
        if cutoff > now or (previous is not None and cutoff <= previous):
            raise ValueError("决策时点在未来或非严格递增")
        previous = cutoff
        if (not isinstance(fold.get("period"), str) or not isinstance(fold.get("fixtures"), list)
                or isinstance(fold.get("expected_total"), bool)
                or not isinstance(fold.get("expected_total"), int)
                or fold["expected_total"] < 1 or len(fold["fixtures"]) != fold["expected_total"]):
            raise ValueError("测试池为空或不是所声明的完整输入池")
        for row in fold["fixtures"]:
            identity = row.get("match_id")
            if not isinstance(identity, str) or not identity or identity in seen or row.get("period") != fold["period"]:
                raise ValueError("测试比赛ID重复或期号不一致")
            seen.add(identity)
            ko = _datetime(row.get("kickoff_at"), "kickoff_at")
            if cutoff >= ko:
                raise ValueError("测试池含决策时已经开球的比赛")
            if identity in ri and (ri[identity].get("period") != fold["period"]
                                  or _datetime(ri[identity]["kickoff_at"], "result.kickoff_at") != ko):
                raise ValueError("赛果与测试赛程身份时间不一致")
            if identity in ri and model_family=="l1_team_strength":
                fields=(("provider_match_id","provider_home_id","provider_away_id","provider_league_id","season_year","season_type")
                    if identity_mode=="provider_native_espn" else ("home_id","away_id","competition_id"))
                if any(row.get(k) is None or ri[identity].get(k)!=row[k] for k in fields):
                    raise ValueError("L1计分赛果未严格绑定来源/规范球队与赛事ID")
            if identity in hi and _datetime(hi[identity]["kickoff_at"], "history.kickoff_at") != ko:
                raise ValueError("训练历史与测试比赛ID指向不同开球时间")
    previous_predictions, reports, selections = [], [], []
    for fold in folds:
        cutoff = _datetime(fold["cutoff_at"], "cutoff_at")
        # No newer historical label, family or identity feature is invented.
        train = _history(training_history, cutoff)
        if any(r["match_id"] in {x["match_id"] for x in fold["fixtures"]} for r in train):
            raise ValueError("测试比赛进入本期训练集")
        eligible = [r for r in previous_predictions if r["match_id"] in ri
                    and result_times[r["match_id"]] <= cutoff]
        selected = None
        losses = {}
        if len(eligible) >= min_selection_matches:
            losses = {str(k): math.fsum(_brier(r["alternatives"][str(k)]["vectors"]["wdl"],
                                              ri[r["match_id"]]["ft_home"], ri[r["match_id"]]["ft_away"])
                                        for r in eligible) / len(eligible) for k in grid}
            # Tie order favours exact identity; the grid is declared above.
            selected = min(grid, key=lambda k: losses[str(k)])
        selections.append({"period": fold["period"], "cutoff_at": fold["cutoff_at"],
                           "selected_kappa": selected if model_family == "l3_calibration" else None,
                           "selected_ridge": selected if model_family == "l1_team_strength" else None,
                           "selected_parameter": selected, "selection_n": len(eligible),
                           "selection_ids": [r["match_id"] for r in eligible], "past_only_brier": losses})
        ledger = []
        for row in fold["fixtures"]:
            record = {"period": fold["period"], "match_id": row["match_id"],
                      "kickoff_at": row["kickoff_at"], "decision_at": fold["cutoff_at"],
                      "selected_kappa": selected if model_family=="l3_calibration" else None,
                      "selected_ridge": selected if model_family=="l1_team_strength" else None,
                      "selected_parameter":selected, "training_n": len(train),
                      "training_ids": [r["match_id"] for r in train]}
            try:
                if row.get("sport") != "football":
                    raise ValueError("非足球或运动类型未核验")
                _fixture_source(row, cutoff)
                handicap = row.get("official_handicap")
                if handicap is not None:
                    if isinstance(handicap, bool) or not isinstance(handicap, int):
                        raise ValueError("提供的北单让球线不是整数")
                    # Bind the line to its own pre-cutoff source instead of
                    # assuming a fixture timestamp proves every other field.
                    _fixture_source({"fixture_source": row.get("handicap_source")}, cutoff)
                prior = predict_l3(train, asof_at=fold["cutoff_at"], kickoff_at=row["kickoff_at"],
                                   competition_family=None, handicap=handicap, min_history=min_history)
                variants = ({str(k): _candidate(prior, train, k) for k in grid}
                    if model_family == "l3_calibration" else
                    {str(k): _team_candidate(prior, train, row, k, fold["cutoff_at"], min_history, identity_mode) for k in grid})
                chosen = variants[str(selected)]
                record.update(status="predicted_research", alternatives=variants,
                              baseline=variants["None"], candidate=chosen)
                previous_predictions.append(record)
            except (ValueError, TypeError, KeyError) as exc:
                record.update(status="blocked", reason=f"{type(exc).__name__}: {exc}")
            ledger.append(record)
        football_n = sum(r.get("sport") == "football" for r in fold["fixtures"])
        reports.append({"period": fold["period"], "offered_n": len(ledger),
                        "football_offered_n": football_n, "training_n": len(train),
                        "predicted_n": sum(r["status"] == "predicted_research" for r in ledger),
                        "matches": ledger})
    pairs, pending = [], []
    for r in previous_predictions:
        result = ri.get(r["match_id"])
        if result is None or result_times[r["match_id"]] > now:
            pending.append(r["match_id"])
            continue
        scores = tuple(_brier(r[k]["vectors"]["wdl"], result["ft_home"], result["ft_away"])
                       for k in ("baseline", "candidate"))
        exact = f"{result['ft_home']}-{result['ft_away']}"
        category = exact if exact in r["baseline"]["score_31"] else (
            "胜其他" if result["ft_home"] > result["ft_away"] else
            "平其他" if result["ft_home"] == result["ft_away"] else "负其他")
        score_losses = {}
        floored = 0
        for label in ("baseline", "candidate"):
            p = r[label]["score_31"][category]
            floored += p < 1e-15
            score_losses["score31_logloss_" + label] = -math.log(max(p, 1e-15))
        goal_errors = {"goal_error_"+label: math.fsum(sum(map(int,k.split("-")))*p
                            for k,p in r[label]["vectors"]["score"].items()) - result["ft_home"] - result["ft_away"]
                       for label in ("baseline","candidate")}
        pairs.append({"period": r["period"], "match_id": r["match_id"], "baseline": scores[0],
                      "candidate": scores[1], "score31_category": category,
                      "logloss_probability_floor": 1e-15, "floored_probabilities": floored, **score_losses, **goal_errors})
    base = math.fsum(r["baseline"] for r in pairs) / len(pairs) if pairs else None
    cand = math.fsum(r["candidate"] for r in pairs) / len(pairs) if pairs else None
    football_n = sum(r["football_offered_n"] for r in reports)
    numeric_gate=summarize_pairs(pairs,football_offered_n=football_n,predicted_n=len(previous_predictions))
    mean_gate=summarize_goal_means(pairs)
    return {"label": "walk_forward_refit_research_not_release_approval",
            "executed_at": datetime.now(timezone.utc).isoformat(), "evaluated_at": evaluated_at,
            "model_family":model_family,"identity_mode":identity_mode,
            "declared_candidates": list(grid), "selections": selections, "folds": reports,
            "offered_n": sum(r["offered_n"] for r in reports), "football_offered_n": football_n,
            "predicted_n": len(previous_predictions), "paired_n": len(pairs), "pending_ids": pending,
            "prediction_coverage": len(previous_predictions) / football_n if football_n else 0.,
            "settled_scoring_coverage": len(pairs) / football_n if football_n else 0.,
            "brier_definition": "sum((p-y)^2)/3", "paired_scores": pairs,
            "brier_baseline": base, "brier_candidate": cand,
            "delta_brier": cand - base if pairs else None,
            "observed_nondegradation": cand <= base + 1e-12 if pairs else None,
            "numerical_gate": numeric_gate, "goal_mean_gate":mean_gate,
            "research_qualification_gate":bool(numeric_gate["numerical_nondegradation_gate"] and mean_gate["mean_nondegradation_gate"]),
            "production_gate_passed": False,
            "limitations": ["source_timestamps_require_external_archive_audit",
                            "supplied_pool_not_independently_verified", "sample_power_unassessed",
                            "team_strength_research_only" if model_family=="l1_team_strength" else "no_team_strength_in_this_candidate",
                            "historical_cutoffs_are_replay_decisions_not_claimed_generation_times"]}


def main():
    parser = argparse.ArgumentParser(description="逐期重拟合并只用更早期赛果选择校准候选，研究用途")
    parser.add_argument("--history", required=True, help="1665场原始导出格式JSONL，或其他同协议导出")
    parser.add_argument("--history-format", choices=("normalized", "legacy1665"), default="normalized")
    parser.add_argument("--folds", required=True, help="完整逐期足球输入池JSON数组")
    parser.add_argument("--results", required=True, help="规范化真实赛果JSONL")
    parser.add_argument("--out", required=True, help="独占创建的研究报告JSON")
    parser.add_argument("--model-family", choices=("l3_calibration","l1_team_strength"), default="l3_calibration")
    parser.add_argument("--identity-mode", choices=("canonical","provider_native_espn"), default="canonical")
    args = parser.parse_args()
    results = [json.loads(l) for l in Path(args.results).read_text().splitlines() if l.strip()]
    history = (load_verified_history(args.history) if args.history_format == "legacy1665"
               else [json.loads(l) for l in Path(args.history).read_text().splitlines() if l.strip()])
    report = run_walk_forward(history=history,
                              folds=json.loads(Path(args.folds).read_text()), results=results,
                              evaluated_at=datetime.now(timezone.utc).isoformat(),
                              model_family=args.model_family,identity_mode=args.identity_mode)
    with Path(args.out).open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, allow_nan=False)
    print(json.dumps({k:v for k,v in report.items() if k not in ("folds", "paired_scores", "selections")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
