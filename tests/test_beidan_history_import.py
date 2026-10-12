"""Check provenance and temporal boundaries of the Muse results export."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from beidan_bd1.history_import import convert_export_row, load_verified_history
from beidan_bd1.baseline import predict_l3


ROW = {
    "lottery_no": "26092", "seq": "1", "league": "杯赛", "home": "甲", "away": "乙",
    "kickoff": "2026-09-04 18:30+08:00", "full_score": "2-1", "half_score": "1-0",
    "handicap": -1, "handicap_collected_at": None, "result_available_at": None,
    "norm_league_id": None, "league_family": None, "source": "beidan_backtest_periods",
    "missing_handicap": False, "provenance_unverified": True,
    "usage": "score_distribution_only", "verified_at": "2026-10-09T06:37:06Z",
}


class ImportTests(unittest.TestCase):
    def test_import_drops_unverified_handicap_and_future_training_is_allowed(self):
        rows = []
        for n in range(31):
            r = dict(ROW, seq=str(n + 1))
            rows.append(convert_export_row(r))
        self.assertEqual(rows[0]["match_id"], "26092:1")
        self.assertNotIn("handicap", rows[0])
        self.assertIsNone(rows[0]["competition_family"])
        with self.assertRaisesRegex(ValueError, "低于影子基线"):
            predict_l3(rows, asof_at="2026-10-08T23:59:00Z",
                       kickoff_at="2026-10-10T10:00:00Z",
                       competition_family="adult_cup", handicap=None)
        p = predict_l3(rows, asof_at="2026-10-09T06:37:07Z",
                       kickoff_at="2026-10-10T10:00:00Z",
                       competition_family="adult_cup", handicap=None)
        self.assertEqual(p["training_n"], 31)
        self.assertEqual(p["family_n"], 0)

    def test_invalid_timestamps_and_scores_fail_closed(self):
        cases = [dict(ROW, verified_at="2026-09-04T09:00:00Z"),
                 dict(ROW, half_score="3-0"), dict(ROW, full_score="2:x"),
                 dict(ROW, result_available_at="2026-09-04T12:00:00Z"),
                 dict(ROW, usage="backtest")]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                convert_export_row(item)

    def test_duplicate_identity_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "results.jsonl"
            path.write_text(json.dumps(ROW) + "\n" + json.dumps(ROW) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "重复"):
                load_verified_history(path)


if __name__ == "__main__":
    unittest.main()
