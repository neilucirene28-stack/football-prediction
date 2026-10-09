"""Settle saved prospective probabilities without refitting or selecting again.

Local digests detect byte changes relative to a retained manifest digest. They
do not independently authenticate provider data, clocks or canonical bindings.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from .baseline import _history
from .evaluation import _brier
from .snapshot import _datetime, build_snapshot
from .walk_forward import _fixture_source, _fixture_identity, _observed_result, summarize_pairs, summarize_goal_means


SCHEMA = "bd1-frozen-bundle-1"
FILES = ("history.jsonl", "folds.json", "report.json", "results.jsonl")
NATIVE_IDS = ("provider_match_id", "provider_home_id", "provider_away_id",
              "provider_league_id", "season_year", "season_type")
CANONICAL_IDS = ("home_id", "away_id", "competition_id")


def _utc_now():
    return datetime.now(timezone.utc)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _archive(bundle):
    raw = {name: (Path(bundle) / name).read_bytes() for name in FILES}
    history = [json.loads(line) for line in raw["history.jsonl"].splitlines() if line.strip()]
    folds = json.loads(raw["folds.json"])
    report = json.loads(raw["report.json"])
    if raw["results.jsonl"].strip():
        raise ValueError("冻结时的赛果文件必须为空；新赛果须另存")
    return raw, history, folds, report


def _check_forecast(value, fixture, cutoff, generated):
    # Reuse six-vector consistency checks only, without claiming input approval.
    checked = build_snapshot(
        match_id=fixture["match_id"], period=fixture["period"],
        kickoff_at=fixture["kickoff_at"], asof_at=cutoff, generated_at=generated,
        model_version="frozen-vector-validation", vectors=value["vectors"],
        sources=[{"name": "numeric_validation_only", "status": "missing", "available_at": None}],
        lambda_home=None, lambda_away=None, handicap=fixture.get("official_handicap"))
    supplied = value.get("score_31")
    expected = checked["score_31"]
    if (not isinstance(supplied, dict) or set(supplied) != set(expected)
            or any(isinstance(supplied[k], bool) or not isinstance(supplied[k], (int, float))
                   or not math.isfinite(supplied[k]) or abs(supplied[k] - expected[k]) > 1e-8
                   for k in expected)):
        raise ValueError("冻结比分31类概率与比分矩阵不一致")
    # Score the exact archived values after checking, rather than a rebuilt fit.
    return value


def _validate_archive(history, folds, report):
    freeze = report.get("prospective_freeze")
    if (not isinstance(freeze, dict) or report.get("paired_n") != 0
            or report.get("brier_baseline") is not None or report.get("brier_candidate") is not None
            or report.get("production_gate_passed") is not False):
        raise ValueError("只接受未结算的前瞻研究冻结，不接受历史重放报告")
    generated = _datetime(report.get("executed_at"), "executed_at")
    started = _datetime(freeze.get("generated_at"), "prospective_freeze.generated_at")
    if started > generated:
        raise ValueError("冻结生成时钟顺序矛盾")
    if not isinstance(folds, list) or not folds or len(folds) != len(report.get("folds", [])):
        raise ValueError("冻结测试池或账本缺失")
    indexed = {row["match_id"]: row for row in history}
    if len(indexed) != len(history):
        raise ValueError("冻结训练历史ID重复")
    pool, predictions = {}, {}
    football_n = 0
    previous = None
    grid = report.get("declared_candidates")
    if not isinstance(grid, list) or not grid or grid[0] is not None:
        raise ValueError("冻结候选列表必须包含None基线")
    for fold, ledger in zip(folds, report["folds"]):
        cutoff = _datetime(fold.get("cutoff_at"), "cutoff_at")
        if cutoff > generated or (previous is not None and cutoff <= previous):
            raise ValueError("冻结决策时钟非法")
        previous = cutoff
        fixtures, matches = fold.get("fixtures"), ledger.get("matches")
        if (not isinstance(fixtures, list) or not fixtures or not isinstance(matches, list)
                or isinstance(fold.get("expected_total"), bool)
                or fold.get("expected_total") != len(fixtures) or len(matches) != len(fixtures)
                or ledger.get("period") != fold.get("period")):
            raise ValueError("冻结完整池与账本不一致")
        available = {row["match_id"] for row in _history(history, cutoff)}
        for fixture, saved in zip(fixtures, matches):
            identity = fixture["match_id"]
            ko = _datetime(fixture["kickoff_at"], "kickoff_at")
            if (identity in pool or not isinstance(identity, str) or not identity
                    or fixture.get("period") != fold.get("period")
                    or saved.get("match_id") != identity or saved.get("period") != fixture["period"]
                    or _datetime(saved.get("kickoff_at"), "saved.kickoff_at") != ko
                    or _datetime(saved.get("decision_at"), "saved.decision_at") != cutoff
                    or not started <= cutoff <= generated < ko):
                raise ValueError("冻结比赛身份、顺序或实际生成时间非法")
            pool[identity] = fixture
            football_n += fixture.get("sport") == "football"
            if saved.get("status") == "blocked":
                continue
            if saved.get("status") != "predicted_research" or fixture.get("sport") != "football":
                raise ValueError("未知冻结状态或非足球预测")
            _fixture_source(fixture, cutoff)
            if fixture.get("official_handicap") is not None:
                _fixture_source({"fixture_source": fixture.get("handicap_source")}, cutoff)
            if report.get("model_family") == "l1_team_strength":
                _fixture_identity(fixture, cutoff, report["identity_mode"])
            training = saved.get("training_ids")
            if (not isinstance(training, list) or len(training) != len(set(training))
                    or saved.get("training_n") != len(training)
                    or not set(training) <= available or identity in training):
                raise ValueError("冻结训练血缘或可得时间非法")
            variants = saved.get("alternatives")
            if not isinstance(variants, dict) or set(variants) != {str(k) for k in grid}:
                raise ValueError("冻结候选概率不完整")
            chosen = str(saved.get("selected_parameter"))
            if chosen not in variants or saved.get("baseline") != variants["None"] or saved.get("candidate") != variants[chosen]:
                raise ValueError("冻结选择与保存的基线/候选不一致")
            for value in variants.values():
                _check_forecast(value, fixture, fold["cutoff_at"], report["executed_at"])
                ids = value.get("training_ids")
                if ids is not None and (len(ids) != len(set(ids)) or not set(ids) <= available
                                        or value.get("training_n") != len(ids) or identity in ids):
                    raise ValueError("球队候选训练血缘非法")
            predictions[identity] = saved
    if (report.get("offered_n") != len(pool) or report.get("football_offered_n") != football_n
            or report.get("predicted_n") != len(predictions)
            or report.get("pending_ids") != list(predictions)):
        raise ValueError("冻结覆盖统计与完整账本不一致")
    return pool, predictions, generated


def seal_bundle(bundle):
    """Add an exclusive digest manifest using the actual current clock."""
    raw, history, folds, report = _archive(bundle)
    pool, predictions, generated = _validate_archive(history, folds, report)
    sealed = _utc_now()
    if sealed < generated or any(sealed >= _datetime(f["kickoff_at"], "kickoff_at") for f in pool.values()):
        raise ValueError("只能在完整输入池开球之前封存；不能补造旧赛前封存时间")
    manifest = {"schema": SCHEMA, "sealed_at": sealed.isoformat(),
                "generation_completed_at": generated.isoformat(),
                "files": {name: {"sha256": _digest(data), "size_bytes": len(data)} for name, data in raw.items()},
                "offered_n": len(pool), "predicted_n": len(predictions),
                "production_gate_passed": False,
                "independent_clock_authentication": False}
    data = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    with (Path(bundle) / "freeze_manifest.json").open("xb") as stream:
        stream.write(data)
    return {"manifest_sha256": _digest(data), **manifest}


def load_bundle(bundle, *, manifest_sha256, evaluated_at):
    """Verify against the digest retained separately from the mutable files."""
    data = (Path(bundle) / "freeze_manifest.json").read_bytes()
    if not _sha(manifest_sha256) or _digest(data) != manifest_sha256:
        raise ValueError("冻结清单与独立保留的SHA256不一致")
    manifest = json.loads(data)
    if manifest.get("schema") != SCHEMA or set(manifest.get("files", {})) != set(FILES):
        raise ValueError("冻结清单协议或文件集合不正确")
    raw, history, folds, report = _archive(bundle)
    for name, content in raw.items():
        if manifest["files"][name] != {"sha256": _digest(content), "size_bytes": len(content)}:
            raise ValueError("冻结文件字节改变: " + name)
    pool, predictions, generated = _validate_archive(history, folds, report)
    sealed = _datetime(manifest.get("sealed_at"), "sealed_at")
    now = _datetime(evaluated_at, "evaluated_at")
    if (not generated <= sealed <= now
            or manifest.get("generation_completed_at") != generated.isoformat()
            or any(sealed >= _datetime(f["kickoff_at"], "kickoff_at") for f in pool.values())
            or manifest.get("offered_n") != len(pool) or manifest.get("predicted_n") != len(predictions)):
        raise ValueError("冻结清单时钟或统计矛盾")
    return report, pool, predictions


def _score(value, result, handicap):
    vectors = value["vectors"]
    h, a = result["ft_home"], result["ft_away"]
    exact = f"{h}-{a}"
    category = exact if exact in value["score_31"] else ("胜其他" if h > a else "平其他" if h == a else "负其他")
    logloss = lambda p: -math.log(max(p, 1e-15))
    total = h + a
    out = {"brier": _brier(vectors["wdl"], h, a), "score31_category": category,
           "score31_logloss": logloss(value["score_31"][category]),
           "floored_probabilities": int(value["score_31"][category] < 1e-15),
           "goal_error": math.fsum(sum(map(int, k.split("-"))) * p for k, p in vectors["score"].items()) - total,
           "total_goals_logloss": logloss(vectors["total_goals"][str(total) if total < 7 else "7+"]),
           "odd_even_logloss": logloss(vectors["odd_even"][("上" if total >= 3 else "下") + ("单" if total % 2 else "双")]),
           "handicap_brier": _brier(vectors["handicap_wdl"], h + handicap, a) if handicap is not None else None,
           "half_full_logloss": None}
    if result.get("ht_home") is not None:
        outcome = lambda x, y: "胜" if x > y else "平" if x == y else "负"
        out["half_full_logloss"] = logloss(vectors["half_full"][outcome(result["ht_home"], result["ht_away"]) + outcome(h, a)])
    return out


def settle_bundle(bundle, *, manifest_sha256, results, evaluated_at):
    report, pool, predictions = load_bundle(bundle, manifest_sha256=manifest_sha256, evaluated_at=evaluated_at)
    now = _datetime(evaluated_at, "evaluated_at")
    indexed, clocks = {}, {}
    for result in results:
        identity = result.get("match_id")
        if identity not in pool or identity in indexed:
            raise ValueError("赛果重复或不属于冻结的完整测试池")
        fixture = pool[identity]
        observed = _observed_result(result)
        if result.get("period") != fixture["period"] or _datetime(result["kickoff_at"], "kickoff_at") != _datetime(fixture["kickoff_at"], "kickoff_at"):
            raise ValueError("赛果期号或开球时间与冻结比赛不一致")
        if report["model_family"] == "l1_team_strength" and identity in predictions:
            fields = NATIVE_IDS if report["identity_mode"] == "provider_native_espn" else CANONICAL_IDS
            if any(fixture.get(k) is None or result.get(k) != fixture[k] for k in fields):
                raise ValueError("赛果主客、比赛或赛事阶段ID与冻结身份不一致")
        source = result.get("result_source")
        if (not isinstance(source, dict) or source.get("status") != "ok"
                or not isinstance(source.get("name"), str) or not source["name"] or not _sha(source.get("raw_sha256"))
                or _datetime(source.get("available_at"), "result_source.available_at") != observed
                or (result.get("summary_sha256") is not None and result["summary_sha256"] != source["raw_sha256"])):
            raise ValueError("赛果缺少与真实核验时刻一致的原件摘要证据")
        halves = [result.get(k) for k in ("ht_home", "ht_away")]
        if any(x is not None for x in halves) and (any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in halves)
                or halves[0] > result["ft_home"] or halves[1] > result["ft_away"]):
            raise ValueError("显式半场比分非法或不完整")
        indexed[identity], clocks[identity] = result, observed
    pairs, pending, ledger = [], [], []
    for identity, fixture in pool.items():
        if identity not in predictions:
            ledger.append({"match_id": identity, "period": fixture["period"], "status": "blocked_in_original_freeze"})
            continue
        saved = predictions[identity]
        result = indexed.get(identity)
        if result is None or clocks[identity] > now:
            pending.append(identity)
            ledger.append({"match_id": identity, "period": fixture["period"], "status": "pending_verified_result"})
            continue
        values = {k: _score(v, result, fixture.get("official_handicap")) for k, v in saved["alternatives"].items()}
        base, candidate = values["None"], values[str(saved["selected_parameter"]) ]
        pair = {"period": fixture["period"], "match_id": identity, "baseline": base["brier"], "candidate": candidate["brier"],
                "score31_logloss_baseline": base["score31_logloss"], "score31_logloss_candidate": candidate["score31_logloss"],
                "goal_error_baseline": base["goal_error"], "goal_error_candidate": candidate["goal_error"],
                "floored_probabilities": base["floored_probabilities"] + candidate["floored_probabilities"]}
        pairs.append(pair)
        ledger.append({"match_id": identity, "period": fixture["period"], "status": "settled_frozen_probabilities",
                       "selected_parameter": saved["selected_parameter"], "result_available_at": clocks[identity].isoformat(),
                       "result_source": result["result_source"], "actual_ft": [result["ft_home"], result["ft_away"]],
                       "baseline": base, "candidate": candidate, "declared_alternative_scores": values})
    football_n = sum(f.get("sport") == "football" for f in pool.values())
    gate = summarize_pairs(pairs, football_offered_n=football_n, predicted_n=len(predictions))
    means = summarize_goal_means(pairs)
    average = lambda key: math.fsum(p[key] for p in pairs) / len(pairs) if pairs else None
    return {"label": "frozen_prospective_settlement_no_refit", "executed_at": _utc_now().isoformat(),
            "evaluated_at": evaluated_at, "manifest_sha256": manifest_sha256,
            "original_generation_completed_at": report["executed_at"], "refitted": False, "reselected_after_results": False,
            "offered_n": len(pool), "football_offered_n": football_n, "predicted_n": len(predictions),
            "blocked_n": len(pool) - len(predictions), "paired_n": len(pairs), "pending_ids": pending,
            "prediction_coverage": len(predictions) / football_n if football_n else 0.,
            "settled_scoring_coverage": len(pairs) / football_n if football_n else 0.,
            "brier_definition": "sum((p-y)^2)/3", "brier_baseline": average("baseline"), "brier_candidate": average("candidate"),
            "delta_brier": average("candidate") - average("baseline") if pairs else None,
            "paired_scores": pairs, "matches": ledger, "numerical_gate": gate, "goal_mean_gate": means,
            "research_qualification_gate": bool(gate["numerical_nondegradation_gate"] and means["mean_nondegradation_gate"]),
            "production_gate_passed": False,
            "limitations": ["digests_do_not_authenticate_provider_or_clocks", "canonical_bindings_remain_unapproved",
                            "complete_official_pool_and_sample_power_require_independent_audit",
                            "alternative_scores_are_diagnostics_not_retrospective_parameter_selection"]}


def main():
    parser = argparse.ArgumentParser(description="封存并结算原赛前概率，研究用途")
    sub = parser.add_subparsers(dest="action", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--bundle", required=True)
    settle = sub.add_parser("settle")
    settle.add_argument("--bundle", required=True)
    settle.add_argument("--manifest-sha256", required=True)
    settle.add_argument("--results", required=True, help="新核验赛果JSONL，不修改原冻结赛果文件")
    settle.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.action == "seal":
        report = seal_bundle(args.bundle)
        print(json.dumps(report, ensure_ascii=False))
        return
    results = [json.loads(line) for line in Path(args.results).read_text().splitlines() if line.strip()]
    report = settle_bundle(args.bundle, manifest_sha256=args.manifest_sha256, results=results, evaluated_at=_utc_now().isoformat())
    with Path(args.out).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False)
    print(json.dumps({k: v for k, v in report.items() if k not in ("matches", "paired_scores")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
