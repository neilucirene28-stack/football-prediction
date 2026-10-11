"""Numerical comparison only; no outcome selection or production promotion."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from beidan_bd1.baseline import predict_l3
from beidan_bd1.history_import import load_verified_history
from beidan_bd1.mean_preserving import shadow_fixed_mean, OUTCOMES


def stats(score):
    cells = [(tuple(map(int, k.split("-"))), p) for k, p in score.items()]
    mean = math.fsum((h + a) * p for (h, a), p in cells)
    return {"mean": mean,
            "variance": math.fsum((h + a - mean) ** 2 * p for (h, a), p in cells),
            "home_mean": math.fsum(h * p for (h, a), p in cells),
            "away_mean": math.fsum(a * p for (h, a), p in cells),
            "p7plus": math.fsum(p for (h, a), p in cells if h + a >= 7)}


def main():
    root = Path(__file__).resolve().parents[1]
    source = root / "outputs/20261010_179_shadow.json"
    old = json.loads(source.read_text())
    if len(old["predictions"]) != 179 or {r["seq"] for r in old["predictions"]} != set(range(11, 190)):
        raise ValueError("Input roster incomplete")
    history = load_verified_history(root / "data_sample/history.jsonl")
    now = datetime.now(timezone.utc).isoformat()
    records, diagnostics = [], []
    # Predeclare candidates; these are not fitted weights or recommended bets.
    weights = [0., .25, .5, 1.]
    for row in old["predictions"]:
        prior = predict_l3(history, asof_at=now, kickoff_at=row["kickoff_at"],
                           competition_family=None, handicap=row["handicap"])
        base_stats = stats(prior["vectors"]["score"])
        if abs(base_stats["mean"] - row["goal_mean_before"]) > 1e-12:
            raise ValueError("Prior changed since original report; cannot use numerical comparison")
        market = None
        if all(v is not None for v in row["sp"]):
            inv = [1 / v for v in row["sp"]]
            market = {k: v / math.fsum(inv) for k, v in zip(OUTCOMES, inv)}
        for w in weights:
            q = {k: (1 - w) * prior["vectors"]["handicap_wdl"][k] + w * market[k]
                 for k in OUTCOMES} if market else prior["vectors"]["handicap_wdl"]
            candidate = shadow_fixed_mean(prior, target=q, line=row["handicap"])
            if market is None:
                candidate.update(status="missing_sp_prior", target_applied=False)
            elif w == 0:
                candidate.update(status="identity_prior", target_applied=False)
            cs = stats(candidate["vectors"]["score"])
            for vector in candidate["vectors"].values():
                if abs(math.fsum(vector.values()) - 1) > 1e-8:
                    raise ValueError("Vector mass residual exceeded")
            if abs(cs["mean"] - base_stats["mean"]) > 1e-9:
                raise ValueError("Mean preservation failure")
            record = {"period": row["period"], "seq": row["seq"], "match_id": row["match_id"],
                      "home": row["home"], "away": row["away"], "league": row["league"],
                      "kickoff_at": row["kickoff_at"], "line": row["handicap"], "weight": w,
                      "generated_at": now, "status": candidate["status"], "reason": candidate["reason"],
                      "target_applied": candidate["target_applied"], "beta": candidate["beta"],
                      "source_observed_at": None, "observation_only": True,
                      "asof_backtest_eligible": False, "production_eligible": False,
                      "parameters_unvalidated": True, "baseline_stats": base_stats,
                      "candidate_stats": cs, "target_handicap_wdl": q,
                      "vectors": candidate["vectors"], "score_31": candidate["score_31"],
                      "top5": sorted(candidate["vectors"]["score"].items(), key=lambda x: -x[1])[:5]}
            records.append(record)
            diagnostics.append({k: record[k] for k in ("seq", "home", "away", "line", "weight",
                                                      "status", "reason", "baseline_stats", "candidate_stats")})
    summary = []
    for w in weights:
        subset = [r for r in records if r["weight"] == w]
        summary.append({"weight": w, "n": len(subset), "statuses": dict(Counter(r["status"] for r in subset)),
                        "max_abs_mean_drift": max(abs(r["candidate_stats"]["mean"] - r["baseline_stats"]["mean"]) for r in subset),
                        "max_variance": max(r["candidate_stats"]["variance"] for r in subset),
                        "max_p7plus": max(r["candidate_stats"]["p7plus"] for r in subset),
                        "fallback_seqs": [r["seq"] for r in subset if r["status"] == "fallback_prior"]})
    drift = [r["goal_mean_after"] - r["goal_mean_before"] for r in old["predictions"]]
    report = {"label": "numerical_feasibility_not_walk_forward", "generated_at": now,
              "football_n": 179, "sp_present_n": 176, "missing_sp_seqs": [140, 143, 169],
              "original_source": old["source"], "original_report_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "projection_code_sha256": hashlib.sha256((root / "beidan_bd1/mean_preserving.py").read_bytes()).hexdigest(),
              "production_eligible": False, "default_runner_modified": False,
              "mean_preservation_does_not_establish_statistical_unbiasedness": True,
              "original_unconstrained_drift": {"min": min(drift), "max": max(drift),
                                                "mean": math.fsum(drift) / 179},
              "candidate_grid": summary, "diagnostics": diagnostics, "records": records}
    folder = root / "outputs/fixed_mean_20261010"
    folder.mkdir(exist_ok=True)
    path = folder / ("comparison_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, allow_nan=False)
    print(json.dumps({"path": str(path), "original_unconstrained_drift": report["original_unconstrained_drift"],
                      "candidate_grid": summary}, ensure_ascii=False))


if __name__ == "__main__":
    main()
