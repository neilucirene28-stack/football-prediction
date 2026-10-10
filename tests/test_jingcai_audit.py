"""Validate research invariants and absence of target-date calibration leakage."""
import json
from pathlib import Path

import pytest

from engine.poisson import score_matrix, match_probs
from scripts.audit_jingcai_letdraw import conditional_tilt, run


@pytest.mark.parametrize("rq", [-2, -1, 1, 2])
def test_research_tilt_preserves_wdl_and_does_not_mutate_source(rq):
    m = score_matrix(1.8, 1.1, rho=-.13)
    original = [row[:] for row in m]
    q = conditional_tilt(m, rq, .4)
    assert m == original
    assert match_probs(q) == pytest.approx(match_probs(m), abs=1e-12)
    assert sum(sum(row) for row in q) == pytest.approx(1, abs=1e-12)
    assert all(v >= 0 for row in q for v in row)


def test_current_date_result_cannot_change_current_date_fitted_parameter(tmp_path):
    # Explicit synthetic research fixture; not used for any match-performance claim.
    rows = []
    for index, day in enumerate([1, 1, 2, 3]):
        rows.append({"date": f"2026-09-{day:02d}", "jc_no": str(index),
                     "home": "SyntheticHome", "away": "SyntheticAway", "jc_league": "测试",
                     "actual": "H", "score": "1-0", "p": [.5, .25, .25],
                     "lam": [1.6, 1.1], "jc_rq": -1})
    source = tmp_path/"synthetic.jsonl"
    source.write_text("".join(json.dumps(r)+"\n" for r in rows))
    a = run(source, tmp_path/"a", min_train=2)
    rows[2]["actual"], rows[2]["score"] = "A", "0-9"
    source.write_text("".join(json.dumps(r)+"\n" for r in rows))
    b = run(source, tmp_path/"b", min_train=2)
    assert a["chronological_development"]["folds"][0] == b["chronological_development"]["folds"][0]
    assert all(f["train_end_date"] < f["test_date"] for f in b["chronological_development"]["folds"])
