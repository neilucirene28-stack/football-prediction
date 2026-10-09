"""26103 roster ingestion cannot turn stale odds or handicaps into features."""
import json
import tempfile
from pathlib import Path
import unittest

from beidan_bd1.pool_import import load_offered_pool


class PoolImportTests(unittest.TestCase):
    def _files(self, folder):
        shadow = {"lottery_no": "26103", "seq": "3", "match_id": "26103-3",
                  "kickoff": "2026-10-10 15:30", "league": "成人杯赛",
                  "home": "甲", "away": "乙", "missing_flag": False,
                  "observation_only": True, "provenance_unverified": True,
                  "generated_at": None, "available_at": None,
                  "sp_wdl": {"胜": 1.7, "平": 4.0, "负": 5.0}, "handicap": -1}
        skip = {"lottery_no": "26103", "seq": "4", "match_id": "26103-4",
                "kickoff": "2026-10-10 18:30", "league": "青年赛事",
                "home": "丙", "away": "丁", "missing_flag": True,
                "skip_reason": "history_missing", "handicap": 0}
        a, b = Path(folder) / "shadow.jsonl", Path(folder) / "skipped.jsonl"
        a.write_text(json.dumps(shadow, ensure_ascii=False) + "\n")
        b.write_text(json.dumps(skip, ensure_ascii=False) + "\n")
        return a, b, shadow, skip

    def test_full_pool_preserves_skips_and_discards_untimed_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b, _, _ = self._files(folder)
            pool = load_offered_pool(a, b, expected_total=2)
            self.assertEqual([m["status"] for m in pool], ["shadow", "skipped"])
            self.assertEqual(pool[0]["kickoff_at"], "2026-10-10T15:30:00+08:00")
            self.assertIsNone(pool[0]["official_handicap"])
            self.assertIsNone(pool[0]["competition_family"])
            self.assertTrue(all(m["observation_only"] for m in pool))
            self.assertFalse(any("sp_wdl" in m for m in pool))

    def test_missing_or_duplicate_pool_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b, _, _ = self._files(folder)
            with self.assertRaisesRegex(ValueError, "仅2场"):
                load_offered_pool(a, b, expected_total=3)
            b.write_text(a.read_text())
            with self.assertRaisesRegex(ValueError, "重复"):
                load_offered_pool(a, b, expected_total=2)

    def test_unverified_record_cannot_be_promoted(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b, shadow, _ = self._files(folder)
            shadow["observation_only"] = False
            a.write_text(json.dumps(shadow, ensure_ascii=False))
            with self.assertRaisesRegex(ValueError, "不能冒充"):
                load_offered_pool(a, b, expected_total=2)
