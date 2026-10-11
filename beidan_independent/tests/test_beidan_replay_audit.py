"""Replayed folds cannot hide future training results or alter frozen versions."""
import copy
import hashlib
from pathlib import Path
import unittest

from beidan_bd1.replay_audit import audit_chronological_replay
from test_beidan_evaluation import _sample, _adjust


def replay_case():
    base, result = _sample()
    base["model_version"] = "baseline-fixed"
    candidate = copy.deepcopy(base)
    candidate["model_version"] = "candidate-fixed"
    _adjust(candidate, .6, .25, .15)
    history = [{"match_id": "old:1", "kickoff_at": "2026-10-09T18:00:00+08:00",
                "result_available_at": "2026-10-10T09:00:00+08:00"}]
    second_base, second_candidate, second_result = (copy.deepcopy(base),
                                                      copy.deepcopy(candidate),
                                                      copy.deepcopy(result))
    for row in (second_base, second_candidate):
        row.update(period="26104", match_id="26104-1",
                   kickoff_at="2026-10-11T18:00:00+08:00",
                   asof_at="2026-10-11T10:00:00+08:00",
                   generated_at="2026-10-11T10:01:00+08:00")
    second_result.update(period="26104", match_id="26104-1",
                         kickoff_at="2026-10-11T18:00:00+08:00",
                         decision_cutoff_at="2026-10-11T12:00:00+08:00",
                         verified_at="2026-10-11T21:00:00+08:00")
    folds = [
        {"period": "26103", "cutoff_at": result["decision_cutoff_at"],
         "fixtures": [result], "baseline": [base], "candidate": [candidate]},
        {"period": "26104", "cutoff_at": second_result["decision_cutoff_at"],
         "fixtures": [second_result], "baseline": [second_base],
         "candidate": [second_candidate]},
    ]
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    frozen = {"baseline": {"model_version": "baseline-fixed",
                            "frozen_at": "2026-10-10T09:00:00+08:00",
                            "artifact_sha256": digest, "artifact_path": __file__},
              "candidate": {"model_version": "candidate-fixed",
                             "frozen_at": "2026-10-10T09:00:00+08:00",
                             "artifact_sha256": digest, "artifact_path": __file__}}
    return folds, history, frozen


def run(folds, history, frozen):
    return audit_chronological_replay(folds=folds, history=history,
                                      evaluated_at="2026-10-12T00:00:00+08:00",
                                      frozen_models=frozen)


class ReplayTests(unittest.TestCase):
    def test_two_periods_aggregate_brier_without_promotion(self):
        folds, history, frozen = replay_case()
        result = run(folds, history, frozen)
        self.assertEqual((result["offered_n"], result["paired_n"]), (2, 2))
        self.assertLess(result["delta_brier"], 0)
        self.assertFalse(result["production_gate_passed"])

    def test_future_training_result_and_unfrozen_candidate_rejected(self):
        folds, history, frozen = replay_case()
        late = copy.deepcopy(history)
        late[0]["result_available_at"] = "2026-10-10T11:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "尚不可得"):
            run(folds, late, frozen)
        bad = copy.deepcopy(frozen)
        bad["candidate"]["frozen_at"] = "2026-10-10T10:30:00+08:00"
        with self.assertRaisesRegex(ValueError, "尚未冻结"):
            run(folds, history, bad)


if __name__ == "__main__":
    unittest.main()
