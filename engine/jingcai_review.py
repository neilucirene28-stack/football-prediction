"""竞彩账本复盘：版本隔离、单场去重、同样本信号诊断；不写生产参数。"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math

from .jingcai_inputs import aware_datetime
from .jingcai_ledger import validate_live_record

REVIEW_SCHEMA = "jingcai-ledger-review-v1"
SIGNALS = ("model", "market", "elo")


def decoded(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def timestamp(value, name):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name}缺时区")
        return value
    return aware_datetime(value, name)


def prepared_record(row, asof):
    """旧记录有真实生成时间可描述评分；缺输入摘要不得用于权重学习。"""
    try:
        saved = decoded(row.get("payload"))
        if not isinstance(saved, dict):
            return None, "missing_original_payload"
        result, payload = saved.get("result"), saved.get("input")
        if not isinstance(result, dict) or result.get("model") != "jingcai":
            return None, "other_or_unknown_model"
        if not isinstance(payload, dict):
            return None, "missing_original_payload"
        if result.get("evaluation_mode") != "live":
            return None, "not_live_prediction"
        version = row.get("model_version")
        if not version or version == "unknown" or result.get("model_version") != version:
            return None, "missing_or_mismatched_version"
        kickoff = timestamp(row.get("kickoff_at"), "kickoff_at")
        sealed = timestamp(row.get("predicted_at"), "predicted_at")
        settled = timestamp(row.get("settled_at"), "settled_at")
        if settled > asof or settled < kickoff or kickoff >= asof:
            return None, "result_not_available_at_cutoff"
        times = validate_live_record(payload, result, now=sealed, require_hash=False)
        if times["kickoff"] != kickoff:
            return None, "mismatched_kickoff"
        match_id = row.get("match_id")
        if isinstance(match_id, bool) or not isinstance(match_id, int) or match_id <= 0:
            return None, "missing_physical_match_id"
        from scripts.scoreboard import checked_probabilities, score_one
        checked_probabilities([row[k] for k in ("p_home", "p_draw", "p_away")])
        checked_probabilities([result[k] for k in ("p_home", "p_draw", "p_away")])
        for key in ("p_home", "p_draw", "p_away"):
            if abs(float(row[key]) - float(result[key])) > 1e-12:
                return None, "mismatched_stored_probability"
        probs = checked_probabilities(result.get("p_final_full") or
                                     [row["p_home"], row["p_draw"], row["p_away"]])
        for key, value in zip(("p_home", "p_draw", "p_away"), probs):
            if abs(value - row[key]) > .000151:
                return None, "mismatched_full_probability"
        scored = score_one(result, {"home_goals": row["home_goals"],
                                    "away_goals": row["away_goals"],
                                    "ht_home": row.get("ht_home"), "ht_away": row.get("ht_away"),
                                    **{k:row.get(k) for k in ("yellow_home","yellow_away","red_home","red_away")}})
        scored.update({"match_id": match_id, "prediction_id": str(row.get("prediction_id", "")),
                       "model_version": version, "league": row.get("league") or "未知",
                       "kickoff_at": kickoff, "predicted_at": sealed, "settled_at": settled,
                       "probs_full": probs,
                       "probability_precision": "full" if result.get("p_final_full") else "rounded_legacy",
                       "eligible_for_shadow_learning": bool(result.get("input_sha256") and row.get("source"))})
        return scored, None
    except (ValueError, TypeError, KeyError, OverflowError):
        return None, "invalid_or_unbound_record"


def paired_shadow(scored, asof, half_life=90, min_n_eff=15):
    """各可得信号集合独立学习；每场计一次，使用相同样本和全精度损失。"""
    cohorts = defaultdict(list)
    for row in scored:
        if not row["eligible_for_shadow_learning"]:
            continue
        available = tuple(sig for sig in SIGNALS if f"logloss_{sig}_full" in row)
        if len(available) >= 2:
            cohorts[available].append(row)
    output = {}
    for signals, rows in cohorts.items():
        losses = {sig: 0.0 for sig in signals}
        n_eff = 0.0
        for row in rows:
            age = (asof - row["kickoff_at"]).total_seconds() / 86400
            decay = .5 ** (max(age, 0)/half_life)
            n_eff += decay
            for sig in signals:
                losses[sig] += decay * row[f"logloss_{sig}_full"]
        weights = None
        if n_eff >= min_n_eff:
            eta = math.sqrt(8*math.log(len(signals))/n_eff)
            lowest = min(losses.values())
            exp_weights = {sig: math.exp(-eta*(loss-lowest)) for sig,loss in losses.items()}
            total = sum(exp_weights.values())
            weights = {sig: value/total for sig,value in exp_weights.items()}
        output["+".join(signals)] = {"n": len(rows), "n_eff": n_eff, "signals": list(signals),
                                    "losses": losses, "weights": weights,
                                    "mode": "diagnostic_shadow_no_parameter_write",
                                    "qualification": "weights fit on these losses; not out-of-sample performance"}
    return output


def paired_comparisons(scored):
    comparisons = {}
    for first, second in (("model", "market"), ("model", "elo")):
        rows = [r for r in scored if f"brier_{first}_full" in r and f"brier_{second}_full" in r]
        if rows:
            comparisons[f"{first}_vs_{second}"] = {
                "n": len(rows), f"brier_{first}": sum(r[f"brier_{first}_full"] for r in rows)/len(rows),
                f"brier_{second}": sum(r[f"brier_{second}_full"] for r in rows)/len(rows),
                "brier_delta_first_minus_second": sum(r[f"brier_{first}_full"]-r[f"brier_{second}_full"] for r in rows)/len(rows),
                "qualification": "paired descriptive comparison; no causal or promotion claim"}
    return comparisons


def calibration_bins(scored, bins=10):
    output = {}
    for idx, name in enumerate(("home", "draw", "away")):
        cells = []
        for bin_id in range(bins):
            rows = [r for r in scored if min(int(r["probs_full"][idx]*bins), bins-1) == bin_id]
            if rows:
                prediction = sum(r["probs_full"][idx] for r in rows)/len(rows)
                observed = sum(r["outcome"] == idx for r in rows)/len(rows)
                cells.append({"low": bin_id/bins, "high": (bin_id+1)/bins,
                              "n": len(rows), "mean_probability": prediction,
                              "observed_rate": observed, "absolute_gap": abs(prediction-observed)})
        output[name] = {"bins": cells,
                        "ece": sum(c["n"]*c["absolute_gap"] for c in cells)/len(scored) if scored else None,
                        "qualification": "descriptive finite-sample calibration; not independent validation"}
    return output


def build_review(rows, *, asof=None):
    from scripts.scoreboard import aggregate
    asof = asof if asof is not None else datetime.now(timezone.utc)
    asof = timestamp(asof, "asof")
    excluded = Counter(); selected = {}; valid_forecasts = 0
    for row in rows:
        prepared, reason = prepared_record(row, asof)
        if reason:
            excluded[reason] += 1
            continue
        valid_forecasts += 1
        key = (prepared["match_id"], prepared["model_version"])
        previous = selected.get(key)
        if previous is not None:
            excluded["additional_forecast_same_match_version"] += 1
        if previous is None or (prepared["predicted_at"], prepared["prediction_id"]) < (previous["predicted_at"], previous["prediction_id"]):
            selected[key] = prepared
    scored = list(selected.values())
    versions = sorted({r["model_version"] for r in scored})
    by_version = {}
    for version in versions:
        subset = [r for r in scored if r["model_version"] == version]
        by_version[version] = {
            "metrics": aggregate(subset), "physical_matches": len(subset),
            "by_league": {league: aggregate([r for r in subset if r["league"] == league])
                          for league in sorted({r["league"] for r in subset})},
            "paired_signals": paired_comparisons(subset), "calibration": calibration_bins(subset),
            "shadow_by_signal_cohort": paired_shadow(subset, asof),
            "not_eligible_for_shadow_learning": sum(not r["eligible_for_shadow_learning"] for r in subset),
        }
    return {"status": "ok" if scored else "no_data", "scope": "jingcai_only",
            "review_schema": REVIEW_SCHEMA, "metric_schema": "multiclass_brier_sum-v2",
            "evaluated_asof": asof.isoformat(), "input_rows": len(rows),
            "valid_forecast_rows_before_deduplication": valid_forecasts,
            "n": len(scored),
            "unique_physical_matches": len({r["match_id"] for r in scored}),
            "match_version_observations": len(scored), "excluded": dict(excluded),
            "selection_policy": "earliest_valid_prematch_prediction_per_physical_match_and_version",
            "by_version": by_version,
            "qualification": "no pooled cross-version learning; hash binds declared input, not authenticated source lineage",
            "note": "未知来源、赛后或重放预测不进入竞彩评分；旧回填不能自动成为学习样本。"}


def load_review_rows(conn, *, days, asof, limit=None, version=None):
    if not 1 <= days <= 3650:
        raise ValueError("days必须在1～3650之间")
    sql = """SELECT p.prediction_id, p.match_id, p.model_version, p.league,
                    p.kickoff_at, p.predicted_at, p.p_home, p.p_draw, p.p_away,
                    p.payload, s.home_goals, s.away_goals, s.ht_home, s.ht_away,
                    s.yellow_home, s.yellow_away, s.red_home, s.red_away, s.settled_at, s.source
             FROM predictions p JOIN settlements s ON s.prediction_id = p.prediction_id
             WHERE p.kickoff_at >= %s AND p.kickoff_at < %s AND s.settled_at <= %s
               AND p.payload->'result'->>'model' = 'jingcai'
               AND COALESCE(p.payload->'result'->>'project_scope', 'jingcai') = 'jingcai'"""
    params = [asof-timedelta(days=days), asof, asof]
    if version is not None:
        sql += " AND p.model_version = %s"
        params.append(version)
    sql += " ORDER BY p.kickoff_at DESC, p.predicted_at, p.prediction_id"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()
