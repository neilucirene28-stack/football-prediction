import copy
import json
from pathlib import Path
import unittest
from beidan_bd1.espn_fixture import audit_fixture


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.raw = (root / "data_sample/espn_scoreboard_bb0c436/scoreboard_ger.2_20261009.json").read_bytes()
        cls.row = next(r for r in json.loads((root / "outputs/20261010_179_shadow.json").read_text())["predictions"] if r["seq"] == 18)

    def run_audit(self, raw=None, roster=None, home="6418", away="130"):
        return audit_fixture(self.raw if raw is None else raw, event_id="401885100", league_slug="ger.2",
            expected_home_id=home, expected_away_id=away, roster=roster or self.row,
            verified_at="2026-10-09T10:10:00Z")

    def test_real_future_fixture_consistency_does_not_approve_identity(self):
        r = self.run_audit()
        self.assertTrue(r["provider_structure_consistent"])
        self.assertFalse(r["canonical_identity_approved"])
        self.assertFalse(r["production_eligible"])
        self.assertFalse(r["external_bookmaker_handicap_imported"])
        self.assertFalse(r["scheduled_placeholder_score_imported"])
        self.assertEqual(r["official_handicap"], self.row["handicap"])

    def test_swapped_sides_time_or_nonfootball_reject(self):
        with self.assertRaises(ValueError):
            self.run_audit(home="130", away="6418")
        for field, value in (("sport", "ice_hockey"), ("kickoff_at", "2026-10-09T17:00:00Z")):
            row = {**self.row, field: value}
            with self.assertRaises(ValueError):
                self.run_audit(roster=row)

    def test_wrong_sport_event_or_live_state_reject(self):
        for live in (False, True):
            p = json.loads(self.raw)
            if live:
                p["events"][0]["competitions"][0]["status"]["type"]["state"] = "in"
            else:
                p["leagues"][0]["uid"] = "s:70~l:3927"
            with self.assertRaises(ValueError):
                self.run_audit(raw=json.dumps(p).encode())


if __name__ == "__main__":
    unittest.main()
