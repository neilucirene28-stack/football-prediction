"""Nonidentity market/draw adjustment really propagates to all score markets."""
import unittest

from beidan_bd1.baseline import predict_l3, _vectors_from_matrix
from beidan_bd1.probability_chain import market_wdl, shadow_adjust
from beidan_bd1.snapshot import build_snapshot
from test_beidan_baseline import sample_history


ASOF = "2026-10-09T14:00:00+08:00"
MARKET = {"event_space": "unhandicapped_wdl", "period": "full_time", "line": None,
          "odds_type": "european_decimal", "selection_order": ["胜", "平", "负"],
          "odds": [2.0, 3.6, 4.0], "available_at": "2026-10-09T13:00:00+08:00",
          "source": "audited_decimal_odds"}


class ProbabilityChainTests(unittest.TestCase):
    def test_identity_and_nonidentity_six_market_propagation(self):
        p = predict_l3(sample_history(), asof_at=ASOF,
                       kickoff_at="2026-10-10T15:00:00+08:00",
                       competition_family=None, handicap=-1)
        identity = shadow_adjust(p, asof_at=ASOF)
        self.assertEqual(identity["vectors"], p["vectors"])
        candidate = shadow_adjust(p, asof_at=ASOF, market=MARKET,
                                  weight=.4, draw_bias=.1)
        self.assertNotEqual(candidate["wdl_after"], p["vectors"]["wdl"])
        self.assertNotEqual(candidate["vectors"]["score"], p["vectors"]["score"])
        self.assertNotEqual(candidate["vectors"]["handicap_wdl"], p["vectors"]["handicap_wdl"])
        self.assertNotEqual(candidate["vectors"]["half_full"], p["vectors"]["half_full"])
        self.assertAlmostEqual(sum(candidate["score_31"].values()), 1)
        self.assertAlmostEqual(candidate["goals_before"], candidate["goals_after"], places=9)
        self.assertEqual(candidate["status"], "projected_fixed_mean")
        snap = build_snapshot(
            match_id="26103-1", period="26103", kickoff_at=p["kickoff_at"],
            asof_at=ASOF, generated_at="2026-10-09T14:01:00+08:00",
            model_version="bd1-market-candidate-shadow", vectors=candidate["vectors"],
            sources=[{"name": "audited_decimal_odds", "available_at": MARKET["available_at"],
                      "status": "ok"},
                     {"name": "verified_results", "available_at": MARKET["available_at"],
                      "status": "ok"}],
            input_sources={"fixture": "audited_decimal_odds", "history": "verified_results",
                           "market": "audited_decimal_odds", "handicap": "audited_decimal_odds"},
            training_lineage={"match_ids": p["training_match_ids"],
                              "latest_available_at": MARKET["available_at"]},
            market_provenance=candidate["market_provenance"],
            lambda_home=None, lambda_away=None, handicap=-1)
        self.assertAlmostEqual(sum(snap["vectors"]["wdl"].values()), 1)
        self.assertTrue(snap["as_of_backtest_eligible"])
        with self.assertRaisesRegex(ValueError, "市场候选快照"):
            build_snapshot(
                match_id="26103-2", period="26103", kickoff_at=p["kickoff_at"],
                asof_at=ASOF, generated_at="2026-10-09T14:01:00+08:00",
                model_version="bd1-market-candidate-shadow", vectors=candidate["vectors"],
                sources=snap["sources"], lambda_home=None, lambda_away=None, handicap=-1)

    def test_wrong_market_event_or_late_observation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "事件空间"):
            market_wdl({**MARKET, "event_space": "handicap_wdl", "line": -1}, asof_at=ASOF)
        with self.assertRaisesRegex(ValueError, "时间"):
            market_wdl({**MARKET, "available_at": "2026-10-09T15:00:00+08:00"}, asof_at=ASOF)
        with self.assertRaisesRegex(ValueError, "类型"):
            market_wdl({**MARKET, "odds_type": "beidan_reference_sp"}, asof_at=ASOF)

    def test_draw_and_temperature_candidates_preserve_mean_and_event_space(self):
        for line in (-1, 0, 1):
            p = predict_l3(sample_history(), asof_at=ASOF,
                           kickoff_at="2026-10-10T15:00:00+08:00",
                           competition_family=None, handicap=line)
            for weight, bias, temperature in ((0, -.5, .8), (0, .5, 1.2), (.7, .2, 1.1)):
                c = shadow_adjust(p, asof_at=ASOF, market=MARKET,
                                  weight=weight, draw_bias=bias, temperature=temperature)
                self.assertEqual(c["status"], "projected_fixed_mean")
                self.assertAlmostEqual(c["goals_before"], c["goals_after"], places=9)
                for key in ("胜", "平", "负"):
                    self.assertAlmostEqual(c["vectors"]["wdl"][key], c["target_wdl"][key], places=9)
                for vector in c["vectors"].values():
                    self.assertAlmostEqual(sum(vector.values()), 1, places=9)
                self.assertFalse(c["production_eligible"])

    def test_infeasible_draw_target_retains_prior_without_relaxing_mean(self):
        vectors, score31 = _vectors_from_matrix([[.8, .1], [.1, 0]], .5, .5, 0)
        prior = {"vectors": vectors, "score_31": score31, "handicap": 0,
                 "ht_fractions": {"home": .5, "away": .5}}
        c = shadow_adjust(prior, asof_at=ASOF, draw_bias=-3)
        self.assertEqual(c["status"], "fallback_prior")
        self.assertFalse(c["target_applied"])
        self.assertEqual(c["vectors"], vectors)
        self.assertEqual(c["wdl_after"], vectors["wdl"])
        self.assertNotEqual(c["target_wdl"], c["wdl_after"])
        self.assertEqual(c["goals_before"], c["goals_after"])


if __name__ == "__main__":
    unittest.main()
