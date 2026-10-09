"""Nonidentity market/draw adjustment really propagates to all score markets."""
import unittest

from beidan_bd1.baseline import predict_l3
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
        self.assertNotAlmostEqual(candidate["goals_before"], candidate["goals_after"])
        snap = build_snapshot(
            match_id="26103-1", period="26103", kickoff_at=p["kickoff_at"],
            asof_at=ASOF, generated_at="2026-10-09T14:01:00+08:00",
            model_version="bd1-market-candidate-shadow", vectors=candidate["vectors"],
            sources=[{"name": "audited_decimal_odds", "available_at": MARKET["available_at"],
                      "status": "ok"}], lambda_home=None, lambda_away=None, handicap=-1)
        self.assertAlmostEqual(sum(snap["vectors"]["wdl"].values()), 1)

    def test_wrong_market_event_or_late_observation_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "事件空间"):
            market_wdl({**MARKET, "event_space": "handicap_wdl", "line": -1}, asof_at=ASOF)
        with self.assertRaisesRegex(ValueError, "时间"):
            market_wdl({**MARKET, "available_at": "2026-10-09T15:00:00+08:00"}, asof_at=ASOF)
        with self.assertRaisesRegex(ValueError, "类型"):
            market_wdl({**MARKET, "odds_type": "beidan_reference_sp"}, asof_at=ASOF)


if __name__ == "__main__":
    unittest.main()
