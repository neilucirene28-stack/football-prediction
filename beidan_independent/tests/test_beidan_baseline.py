"""L3 赛事族先验的时间隔离和六玩法自洽性。"""
import copy
import math
import unittest

from beidan_bd1.baseline import predict_l3, _root_matrix
from beidan_bd1.snapshot import build_snapshot
from beidan_bd1.snapshot import _SCORE_HOME, _SCORE_DRAW, _SCORE_AWAY


def sample_history():
    rows = []
    for i in range(35):
        h, a = ((2, 1), (0, 0), (0, 2), (3, 2), (1, 1))[i % 5]
        rows.append({
            "match_id": f"past-{i}", "competition_family": "adult_cup" if i % 2 else "adult_league",
            "kickoff_at": "2026-09-01T10:00:00+08:00",
            "result_available_at": "2026-09-01T13:00:00+08:00",
            "regular_time": True, "ft_home": h, "ft_away": a,
            "ht_home": min(h, 1), "ht_away": min(a, 1),
        })
    return rows


def call(rows):
    return predict_l3(rows, asof_at="2026-10-09T14:00:00+08:00",
                      kickoff_at="2026-10-10T15:30:00+08:00",
                      competition_family="adult_cup", handicap=-1)


class BaselineTests(unittest.TestCase):
    def test_fulltime_score_market_has_31_categories(self):
        self.assertEqual((len(_SCORE_HOME), len(_SCORE_DRAW), len(_SCORE_AWAY)), (12, 4, 12))
        prediction = call(sample_history())
        self.assertIn("5-0", prediction["score_31"])
        self.assertIn("0-5", prediction["score_31"])
        self.assertIn("2-5", prediction["score_31"])
        self.assertIn("胜其他", prediction["score_31"])
        self.assertIn("负其他", prediction["score_31"])

    def test_six_vectors_and_snapshot_contract(self):
        prediction = call(sample_history())
        self.assertTrue(prediction["parameters_unvalidated"])
        self.assertEqual(prediction["route"], "L3_prior_only")
        self.assertEqual(len(prediction["score_31"]), 31)
        self.assertAlmostEqual(sum(prediction["score_31"].values()), 1)
        record = build_snapshot(
            match_id="26103-999", period="26103",
            kickoff_at=prediction["kickoff_at"],
            asof_at=prediction["asof_at"],
            generated_at="2026-10-09T14:01:00+08:00",
            model_version=prediction["model_version"],
            vectors=prediction["vectors"],
            sources=[{"name": "verified_history", "source_match_id": "fixture-1",
                      "available_at": "2026-10-09T13:00:00+08:00", "status": "ok"}],
            lambda_home=None, lambda_away=None, handicap=-1)
        self.assertAlmostEqual(sum(record["vectors"]["half_full"].values()), 1)

    def test_future_results_and_nonregular_scores_cannot_change_prediction(self):
        past = sample_history()
        baseline = call(past)
        future = copy.deepcopy(past[0])
        future.update(match_id="future", ft_home=15, ft_away=0,
                      ht_home=10, ht_away=0,
                      kickoff_at="2026-10-09T15:00:00+08:00",
                      result_available_at="2026-10-09T17:00:00+08:00")
        late_result = copy.deepcopy(future)
        late_result.update(match_id="late-result", kickoff_at="2026-10-08T15:00:00+08:00")
        penalty = copy.deepcopy(future)
        penalty.update(match_id="penalty", kickoff_at="2026-09-01T10:00:00+08:00",
                       result_available_at="2026-09-01T13:00:00+08:00",
                       regular_time=False)
        altered = call(past + [future, late_result, penalty])
        self.assertEqual(baseline["training_match_ids"], altered["training_match_ids"])
        self.assertEqual(baseline["vectors"], altered["vectors"])

    def test_insufficient_data_refuses_prediction(self):
        with self.assertRaisesRegex(ValueError, "低于影子基线"):
            call(sample_history()[:4])

    def test_verified_today_is_not_available_in_past(self):
        rows = sample_history()
        for r in rows:
            r["result_available_at"] = None
            r["verified_at"] = "2026-10-09T14:30:00+08:00"
        with self.assertRaisesRegex(ValueError, "低于影子基线"):
            call(rows)  # asof=14:00，不能倒填今日14:30核验的旧赛果
        present = predict_l3(rows, asof_at="2026-10-09T15:00:00+08:00",
                             kickoff_at="2026-10-10T15:30:00+08:00",
                             competition_family="adult_cup", handicap=-1)
        self.assertEqual(present["training_n"], 35)

    def test_missing_handicap_keeps_other_vectors(self):
        rows = sample_history()
        output = predict_l3(rows, asof_at="2026-10-09T14:00:00+08:00",
                            kickoff_at="2026-10-10T15:30:00+08:00",
                            competition_family="adult_cup", handicap=None)
        self.assertIsNone(output["vectors"]["handicap_wdl"])
        self.assertEqual(len(output["vectors"]["total_goals"]), 8)

    def test_unknown_family_uses_root_without_inventing_mapping(self):
        output = predict_l3(sample_history(), asof_at="2026-10-09T14:00:00+08:00",
                            kickoff_at="2026-10-10T15:30:00+08:00",
                            competition_family=None, handicap=None)
        self.assertEqual(output["family_status"], "unknown_root_fallback")
        self.assertEqual(output["family_n"], 0)
        self.assertIsNone(output["competition_family"])
        self.assertAlmostEqual(sum(output["vectors"]["wdl"].values()), 1)

    def test_poisson_root_parity_oracle(self):
        rows = sample_history()
        matrix = _root_matrix(rows)
        mu = ((sum(r["ft_home"] for r in rows) + .5)
              + (sum(r["ft_away"] for r in rows) + .5)) / len(rows)
        odd = sum(p for h, row in enumerate(matrix) for a, p in enumerate(row)
                  if (h + a) % 2)
        self.assertLess(abs(odd - (1 - math.exp(-2 * mu)) / 2), 1e-7)


if __name__ == "__main__":
    unittest.main()
