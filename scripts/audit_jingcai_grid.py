#!/usr/bin/env python3
"""Offline numerical grid comparison, not a new pre-match performance claim."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.jingcai_grid import score_grid, TAIL_TOLERANCE
from engine.poisson import (score_matrix, pmf, ipf_to_marginals, handicap_1x2,
                            match_probs, half_full_1x2)
from engine.letdraw import apply_letdraw_to_matrix
from scripts.audit_jingcai_letdraw import load, outcome, brier


def run(source, destination):
    rows, digest = load(source)
    old_brier = new_brier = 0
    max_delta = max_old_tail = max_new_tail = 0
    changed_picks = enlarged = 0
    limits = Counter()
    for row in rows:
        lh, la = row["lam"]
        upper, tail = score_grid(lh, la)
        limits[upper] += 1
        enlarged += upper > 10
        old_tail = max(0, 1 - sum(pmf(k, lh) for k in range(11)) * sum(pmf(k, la) for k in range(11)))
        max_old_tail, max_new_tail = max(max_old_tail, old_tail), max(max_new_tail, tail)
        probabilities = []
        for cap in (10, upper):
            matrix = ipf_to_marginals(score_matrix(lh, la, rho=-.13, max_goals=cap), row["p"])
            matrix = apply_letdraw_to_matrix(matrix, row["jc_rq"], league=row["jc_league"], strength=.5)
            probabilities.append(handicap_1x2(matrix, row["jc_rq"]))
        old, new = probabilities
        y = outcome(*map(int, row["score"].split("-")), row["jc_rq"])
        old_brier += brier(old, y); new_brier += brier(new, y)
        max_delta = max(max_delta, *(abs(a-b) for a,b in zip(old, new)))
        changed_picks += max(range(3), key=old.__getitem__) != max(range(3), key=new.__getitem__)

    # Deliberately high lambdas to expose the legacy tail approximation.
    lh, la = 4.5, 4.0
    upper, tail = score_grid(lh, la)
    old_matrix = ipf_to_marginals(score_matrix(lh, la, rho=-.13), (.6, .25, .15))
    new_matrix = ipf_to_marginals(score_matrix(lh, la, rho=-.13, max_goals=upper), (.6, .25, .15))
    def marginal_error(matrix, strict):
        hf = half_full_1x2(lh, la, rho=-.13, ft_matrix=matrix, strict_ft_grid=strict)
        probs = [sum(v for key,v in hf.items() if key[-1] == k) for k in ("胜", "平", "负")]
        return max(abs(a-b) for a,b in zip(probs, match_probs(matrix)))
    report = {
        "scope": "jingcai_numerical_diagnostic", "source_sha256": digest,
        "n": len(rows), "grid_limits": dict(limits), "enlarged_grids": enlarged,
        "poisson_joint_tail_tolerance": TAIL_TOLERANCE,
        "max_old_poisson_joint_tail": max_old_tail, "max_new_poisson_joint_tail": max_new_tail,
        "max_handicap_probability_change": max_delta, "changed_argmax_picks": changed_picks,
        "diagnostic_brier_sum_old_grid": old_brier/len(rows),
        "diagnostic_brier_sum_adaptive_grid": new_brier/len(rows),
        "stress_case": {"lambdas": [lh, la], "adaptive_max_goals": upper,
                        "poisson_joint_tail": tail,
                        "legacy_half_full_wdl_marginal_error": marginal_error(old_matrix, False),
                        "strict_half_full_wdl_marginal_error": marginal_error(new_matrix, True)},
        "limitations": ["rounded lambdas/p; rho assumed -0.13",
                        "fixed prior includes future dates for this September sample",
                        "numerical truncation bound applies to raw Poisson/DC, not forecast accuracy",
                        "not a certified pre-match replay, independent test or profitability result"],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT/"hidden_backfill_jingcai.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT/"audit_output/jingcai-letdraw/grid_audit.json")
    args = parser.parse_args()
    print(json.dumps(run(args.input, args.output), ensure_ascii=False, indent=2))
