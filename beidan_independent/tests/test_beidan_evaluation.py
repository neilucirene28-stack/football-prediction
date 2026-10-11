"""Paired audit cannot promote unverified data or selective coverage."""
import copy
import unittest

from beidan_bd1.evaluation import audit_frozen_pair
from beidan_bd1.snapshot import SCHEMA_VERSION


def _sample():
    snap = {
        "schema_version": SCHEMA_VERSION, "status": "shadow", "handicap": None,
        "model_version": "bd1-test", "lambda_home": None, "lambda_away": None,
        "period": "26103", "match_id": "26103-1", "kickoff_at": "2026-10-10T18:00:00+08:00",
        "sport": "football",
        "asof_at": "2026-10-10T10:00:00+08:00", "generated_at": "2026-10-10T10:01:00+08:00",
        "observation_only": False, "as_of_backtest_eligible": True,
        "provenance_unverified": False,
        "sources": [{"name": "verified", "status": "ok", "available_at": "2026-10-10T09:00:00+08:00"}],
        "input_sources": {"fixture": "verified", "history": "verified", "family": "verified"},
        "training_lineage": {"match_ids": ["old:1"],
                             "latest_available_at": "2026-10-10T09:00:00+08:00"},
        "vectors": {
            "wdl": {"胜": .5, "平": .3, "负": .2}, "handicap_wdl": None,
            "score": {"1-0": .5, "0-0": .3, "0-1": .2},
            "total_goals": {**{str(i): (.3 if i == 0 else .7 if i == 1 else 0.) for i in range(7)}, "7+": 0.},
            "half_full": {a+b: (.5 if a+b == "胜胜" else .3 if a+b == "平平" else .2 if a+b == "负负" else 0.)
                          for a in ("胜", "平", "负") for b in ("胜", "平", "负")},
            "odd_even": {"上单": 0., "上双": 0., "下单": .7, "下双": .3},
        },
    }
    result = {
        "period": "26103", "match_id": "26103-1", "kickoff_at": snap["kickoff_at"],
        "decision_cutoff_at": "2026-10-10T12:00:00+08:00", "ft_home": 1, "ft_away": 0,
        "verified_at": "2026-10-10T21:00:00+08:00", "result_available_at": None,
    }
    return snap, result


def _adjust(snap, win, draw, lose):
    v = snap["vectors"]
    v["wdl"] = {"胜": win, "平": draw, "负": lose}
    v["score"] = {"1-0": win, "0-0": draw, "0-1": lose}
    v["total_goals"].update({"0": draw, "1": win + lose})
    v["half_full"].update({"胜胜": win, "平平": draw, "负负": lose})
    v["odd_even"].update({"下单": win + lose, "下双": draw})


def _audit(base, candidate, results):
    return audit_frozen_pair(baseline=base, candidate=candidate, fixtures=results,
                             evaluated_at="2026-10-11T00:00:00+08:00")


class AuditTests(unittest.TestCase):
    def test_paired_delta_and_full_pool_coverage(self):
        snap, result = _sample()
        candidate = copy.deepcopy(snap)
        _adjust(candidate, .6, .25, .15)
        skipped = dict(result, match_id="26103-2", ft_home=0, ft_away=0)
        report = _audit([snap], [candidate], [result, skipped])
        self.assertEqual(report["offered_n"], 2)
        self.assertEqual(report["coverage"], .5)
        self.assertLess(report["delta_brier"], 0)
        self.assertFalse(report["production_gate_passed"])

    def test_unverified_and_late_feature_rejected(self):
        snap, result = _sample()
        for mutation in ({"observation_only": True},
                         {"sources": [{"name": "late", "status": "ok",
                                       "available_at": "2026-10-10T11:00:00+08:00"}]},
                         {"generated_at": "2026-10-10T13:00:00+08:00"}):
            bad = copy.deepcopy(snap)
            bad.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                _audit([snap], [bad], [result])

    def test_selective_coverage_and_unconfirmed_result_rejected(self):
        snap, result = _sample()
        with self.assertRaisesRegex(ValueError, "成对"):
            _audit([snap], [], [result])
        with self.assertRaisesRegex(ValueError, "可信确认"):
            _audit([snap], [snap], [dict(result, verified_at=None)])


if __name__ == "__main__":
    unittest.main()
