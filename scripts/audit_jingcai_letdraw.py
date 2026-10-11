#!/usr/bin/env python3
"""Offline Jingcai audit and chronological calibration development experiment.

Stored lambdas/p are rounded and lack as-of/version evidence. Reconstructions
are diagnostics, not exact production replays or certified pre-match backtests.
No network requests, model parameter writes or production prediction writes.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.poisson import score_matrix, ipf_to_marginals, handicap_1x2, match_probs
from engine.letdraw import apply_letdraw_to_matrix
from engine.jingcai_handicap import matrix_summary, integer_handicap

BIAS_GRID = tuple(round(i * .1, 2) for i in range(-8, 9))
REGULARIZATION = .02


def outcome(h, a, rq=0):
    d = h - a + rq
    return 0 if d > 0 else 1 if d == 0 else 2


def brier(p, y):
    return sum((v - int(i == y)) ** 2 for i, v in enumerate(p))


def conditional_tilt(matrix, rq, bias):
    """Research-only one-parameter calibration within the winning region.

    Preserves full-time WDL mass. Unlike a constrained-moment model, it does
    not preserve goal means; that change is explicitly measured in outputs.
    """
    n = len(matrix)
    in_w = lambda i, j: i > j if rq < 0 else i < j
    w = sum(matrix[i][j] for i in range(n) for j in range(n) if in_w(i, j))
    e = sum(matrix[i][j] for i in range(n) for j in range(n) if i-j == -rq)
    if rq == 0 or bias == 0 or w <= 0 or e <= 0 or e >= w:
        return [row[:] for row in matrix]
    c = e / w
    cn = 1 / (1 + math.exp(-(math.log(c / (1-c)) + bias)))
    fe, fr = cn / c, (1-cn) / (1-c)
    return [[v * (fe if i-j == -rq else fr) if in_w(i, j) else v
             for j, v in enumerate(row)] for i, row in enumerate(matrix)]


def metrics(predictions):
    n = len(predictions)
    if not n:
        return {"n": 0}
    hits = sum(max(range(3), key=lambda i: r["p"][i]) == r["y"] for r in predictions)
    return {"n": n, "brier_sum": sum(brier(r["p"], r["y"]) for r in predictions)/n,
            "draw_binary_brier": sum((r["p"][1] - int(r["y"] == 1))**2 for r in predictions)/n,
            "log_loss": sum(-math.log(max(r["p"][r["y"]], 1e-12)) for r in predictions)/n,
            "hits": hits, "hit_rate": hits/n,
            "draw_picks": sum(max(range(3), key=lambda i: r["p"][i]) == 1 for r in predictions),
            "actual_draw_rate": sum(r["y"] == 1 for r in predictions)/n,
            "mean_draw_probability": sum(r["p"][1] for r in predictions)/n,
            "max_draw_probability": max(r["p"][1] for r in predictions),
            "draw_at_least_028": sum(r["p"][1] >= .28 for r in predictions)}


def load(path):
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    keys = set()
    for r in rows:
        key = (r["date"], r["jc_no"], r["home"], r["away"])
        if key in keys:
            raise ValueError("duplicate match key")
        keys.add(key)
        h, a = (int(x) for x in r["score"].split("-"))
        if h < 0 or a < 0 or r["actual"] != ("H", "D", "A")[outcome(h, a)]:
            raise ValueError("score/actual mismatch")
        p = r["p"]
        if len(p) != 3 or any(not math.isfinite(x) or not 0 <= x <= 1 for x in p) or abs(sum(p)-1) > .0003:
            raise ValueError("bad stored WDL probabilities")
        if len(r["lam"]) != 2 or any(not math.isfinite(x) or x <= 0 for x in r["lam"]):
            raise ValueError("bad lambda")
        integer_handicap(r["jc_rq"])
    return sorted(rows, key=lambda r: (r["date"], r["jc_no"])), hashlib.sha256(raw).hexdigest()


def bootstrap_delta(scored, field="brier_delta", iterations=2000):
    groups = defaultdict(list)
    for r in scored:
        groups[r["date"]].append(r[field])
    days = sorted(groups)
    if not days:
        return None
    rng = random.Random(20261010)
    values = []
    for _ in range(iterations):
        sample = [v for _ in days for v in groups[rng.choice(days)]]
        values.append(sum(sample)/len(sample))
    values.sort()
    return {"point_delta_candidate_minus_baseline": sum(r[field] for r in scored)/len(scored),
            "percentile_95ci": [values[int(.025*iterations)], values[int(.975*iterations)]],
            "resampling_unit": "date", "iterations": iterations,
            "qualification": "development diagnostic; upstream as-of unverified; fitted-model uncertainty not refit"}


def run(source, destination, min_train=60):
    rows, digest = load(source)
    stages = {k: [] for k in ("raw_poisson", "raw_dc", "ipf", "fixed_prior_letdraw")}
    cached, detail = [], []
    sign_errors = 0
    for r in rows:
        rq = r["jc_rq"]
        h, a = map(int, r["score"].split("-"))
        y = outcome(h, a, rq)
        sign_errors += outcome(h, a, -rq) != y
        poisson = score_matrix(*r["lam"], rho=0)
        dc = score_matrix(*r["lam"], rho=-.13)
        ipf = ipf_to_marginals(dc, r["p"])
        fixed = apply_letdraw_to_matrix(ipf, rq, league=r["jc_league"], strength=.5)
        record = {"date": r["date"], "jc_no": r["jc_no"], "rq": rq,
                  "score": r["score"], "actual_handicap": y, "stages": {}}
        for name, m in zip(stages, (poisson, dc, ipf, fixed)):
            p = handicap_1x2(m, rq)
            stages[name].append({"p": p, "y": y})
            record["stages"][name] = {"probabilities": list(p), "brier_sum": brier(p, y)}
        record["fixed_prior_mean_shift"] = (
            sum(matrix_summary(fixed)[k] - matrix_summary(ipf)[k] for k in ("mean_home", "mean_away")))
        detail.append(record)
        cached.append({"row": r, "matrix": ipf, "y": y})

    # Fit the single regularized offset on past dates only, then evaluate next date.
    # Stored upstream features themselves cannot be certified as pre-match.
    fold_log, scored, base_records, candidate_records = [], [], [], []
    dates = sorted({r["date"] for r in rows})
    for date in dates:
        train = [x for x in cached if x["row"]["date"] < date]
        test = [x for x in cached if x["row"]["date"] == date]
        if len(train) < min_train:
            continue
        def objective(bias):
            losses = [brier(handicap_1x2(conditional_tilt(x["matrix"], x["row"]["jc_rq"], bias), x["row"]["jc_rq"]), x["y"]) for x in train]
            return sum(losses)/len(losses) + REGULARIZATION*bias*bias
        bias = min(BIAS_GRID, key=lambda x: (objective(x), abs(x), x))
        fold_log.append({"test_date": date, "train_end_date": max(x["row"]["date"] for x in train),
                         "n_train": len(train), "n_test": len(test), "bias": bias})
        for x in test:
            r, m, y = x["row"], x["matrix"], x["y"]
            cand = conditional_tilt(m, r["jc_rq"], bias)
            pb, pc = handicap_1x2(m, r["jc_rq"]), handicap_1x2(cand, r["jc_rq"])
            wdl_error = max(abs(a-b) for a, b in zip(match_probs(m), match_probs(cand)))
            if wdl_error > 1e-10:
                raise ValueError("candidate WDL mass changed")
            mean_change = sum(matrix_summary(cand)[k]-matrix_summary(m)[k] for k in ("mean_home", "mean_away"))
            scored.append({"date": date, "jc_no": r["jc_no"], "rq": r["jc_rq"],
                           "y": y, "bias": bias, "baseline_probs": list(pb), "candidate_probs": list(pc),
                           "brier_delta": brier(pc,y)-brier(pb,y),
                           "draw_brier_delta": (pc[1]-int(y==1))**2-(pb[1]-int(y==1))**2,
                           "goal_mean_change": mean_change, "max_wdl_error": wdl_error})
            base_records.append({"p": pb, "y": y})
            candidate_records.append({"p": pc, "y": y})

    n = len(rows)
    bins = []
    for lo, hi in ((0,.15), (.15,.2), (.2,.25), (.25,.3), (.3,1.000001)):
        part = [x for x in stages["fixed_prior_letdraw"] if lo <= x["p"][1] < hi]
        if part:
            bins.append({"low":lo,"high":hi,"n":len(part),
                         "mean_prediction":sum(x["p"][1] for x in part)/len(part),
                         "actual_rate":sum(x["y"]==1 for x in part)/len(part)})
    report = {
        "scope": "jingcai_only", "source_sha256": digest, "n": n,
        "date_range": [dates[0],dates[-1]], "rq_counts": dict(Counter(r["jc_rq"] for r in rows)),
        "metric_definition": "three-class squared errors summed per match, then match mean",
        "evidence_counts": {k:sum(bool(r.get(k)) for r in rows) for k in
                            ("model_version","kickoff_at","snapshot_at","prediction_generated_at","feature_as_of")},
        "reconstruction_limitations": ["rounded lambda and stored p; assumed rho=-0.13",
                                       "no original payload or model/calibration version",
                                       "fixed Sep-30 prior is future-informed for all Sep-01~20 rows",
                                       "diagnostic only; no certified production or betting-performance claim"],
        "stages": {name:metrics(vs) for name,vs in stages.items()},
        "settlement_sign_error_changed_actual_category_count": sign_errors,
        "fixed_prior_reliability_bins_diagnostic": bins,
        "fixed_prior_average_total_goal_mean_shift": sum(x["fixed_prior_mean_shift"] for x in detail)/n,
        "chronological_development": {
            "min_train":min_train,"bias_grid":BIAS_GRID,"regularization":REGULARIZATION,
            "baseline":metrics(base_records),"candidate":metrics(candidate_records),
            "brier_delta_interval":bootstrap_delta(scored),
            "draw_brier_delta_interval":bootstrap_delta(scored,"draw_brier_delta"),
            "folds":fold_log, "candidate_deployment":"disabled_research_only",
            "qualification":"only calibration fit is chronological; upstream pre-match lineage remains unverified",
        },
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination/"audit_summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    for filename, recs in (("audit_match_details.jsonl",detail),("chronological_match_details.jsonl",scored)):
        (destination/filename).write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in recs))
    print(json.dumps({"n":n,"dates":report["date_range"],"sign_error_changed_categories":sign_errors,
                      "stages":report["stages"],"development":report["chronological_development"]},ensure_ascii=False,indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,default=ROOT/"hidden_backfill_jingcai.jsonl")
    parser.add_argument("--output",type=Path,default=ROOT/"audit_output/jingcai-letdraw")
    parser.add_argument("--min-train",type=int,default=60)
    args = parser.parse_args()
    if args.min_train < 1:
        parser.error("min-train must be positive")
    run(args.input,args.output,args.min_train)
