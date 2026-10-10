"""Run every offered fixture and retain failures without inventing timestamps."""
import json
import tempfile
from pathlib import Path
import unittest

from beidan_bd1.runner import run_shadow_pool


ASOF = "2026-10-09T15:20:00+08:00"


def history():
    return [{"match_id": f"past-{i}", "competition_family": None,
             "kickoff_at": "2026-09-01T10:00:00+08:00",
             "result_available_at": None, "verified_at": "2026-10-09T14:37:06+08:00",
             "regular_time": True, "ft_home": i % 4, "ft_away": (i + 1) % 3,
             "ht_home": min(1, i % 4), "ht_away": min(1, (i + 1) % 3)}
            for i in range(35)]


def fixture(match_id="26103-1", kickoff="2026-10-10T15:30:00+08:00"):
    return {"period": "26103", "match_id": match_id, "kickoff_at": kickoff,
            "sport": "football",
            "official_handicap": -1, "competition_family": None,
            "sources": [{"name": "roster_source", "available_at":
                         "2026-10-09T14:50:00+08:00", "status": "ok"}],
            "input_sources": {"fixture": "roster_source"}}


class RunnerTests(unittest.TestCase):
    def test_mixed_sport_roster_skips_nonfootball_before_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            tennis = fixture("26103-421")
            tennis["sport"] = "tennis"
            report = run_shadow_pool(fixtures=[tennis, fixture("26103-7")],
                                     history=history(), root=tmp, expected_total=2,
                                     period="26103", synthetic_at=ASOF, run_id="sports")
            self.assertEqual((report["saved_n"], report["skipped_n"]), (1, 1))
            self.assertEqual((report["football_offered_n"], report["nonfootball_offered_n"]),
                             (1, 1))
            self.assertIn("足球模型不适用", report["matches"][0]["reason"])

    def test_manifest_counts_saves_skips_and_no_fake_handicap(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run_shadow_pool(
                fixtures=[fixture(), fixture("26103-2", "2026-10-09T14:00:00+08:00")],
                history=history(), root=tmp, expected_total=2, period="26103",
                synthetic_at=ASOF, run_id="dryrun")
            self.assertEqual((report["offered_n"], report["saved_n"], report["skipped_n"]),
                             (2, 1, 1))
            self.assertEqual(report["eligible_n"], 0)
            self.assertEqual(report["pool_scope"], "supplied_roster_unverified")
            saved = json.loads((Path(tmp) / report["matches"][0]["snapshot_path"]).read_text())
            self.assertIsNone(saved["handicap"])
            self.assertIsNone(saved["vectors"]["handicap_wdl"])
            self.assertTrue(saved["synthetic_sample"])
            self.assertTrue(saved["observation_only"])
            manifest = Path(tmp) / "manifests" / "26103" / "dryrun.json"
            previous = manifest.read_bytes()
            with self.assertRaises(FileExistsError):
                run_shadow_pool(fixtures=[fixture(), fixture("26103-2", "2026-10-09T14:00:00+08:00")],
                                history=history(), root=tmp, expected_total=2,
                                period="26103", synthetic_at=ASOF, run_id="dryrun")
            self.assertEqual(manifest.read_bytes(), previous)

    def test_no_history_or_partial_roster_never_claims_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "预期"):
                run_shadow_pool(fixtures=[fixture()], history=[], root=tmp,
                                expected_total=2, period="26103", synthetic_at=ASOF)
            report = run_shadow_pool(fixtures=[fixture()], history=[], root=tmp,
                                     expected_total=1, period="26103",
                                     synthetic_at=ASOF, run_id="no_history")
            self.assertEqual((report["saved_n"], report["skipped_n"]), (0, 1))
            self.assertIn("低于影子基线", report["matches"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
