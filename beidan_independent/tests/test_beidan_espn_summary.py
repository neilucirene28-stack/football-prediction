import copy
import json
from pathlib import Path
import unittest

from beidan_bd1.espn_summary import audit_summary
from beidan_bd1.espn_schedule import parse_schedule


class SummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "data_sample"
        cls.raw = (root / "espn_j1_dd2deb7/espn_summary_401877614.json").read_bytes()
        cls.payload = json.loads(cls.raw)
        schedule = json.loads((root / "espn_j1_9b4f6b8/espn_team_7476.json").read_bytes())
        rows, _ = parse_schedule(schedule, expected_team_id="7476", verified_at="2026-10-09T10:00:00Z")
        cls.row = next(r for r in rows if r["provider_match_id"] == "401877614")

    def run_audit(self, payload):
        return audit_summary(payload, self.row, verified_at="2026-10-09T10:00:00Z",
                             raw_bytes=json.dumps(payload).encode())

    def test_real_explicit_halves_and_complete_event_crosscheck(self):
        row, report = self.run_audit(self.payload)
        self.assertEqual((row["ft_home"], row["ft_away"], row["ht_home"], row["ht_away"]), (2, 4, 1, 0))
        self.assertTrue(report["complete_goal_events_crosscheck"])
        self.assertEqual(report["goal_events_n"], 6)
        self.assertFalse(row["identity_verified"])
        self.assertFalse(row["full_chain_eligible"])

    def test_no_halftime_inference_even_with_goals(self):
        p = copy.deepcopy(self.payload)
        for c in p["header"]["competitions"][0]["competitors"]:
            c.pop("linescores")
        row, report = self.run_audit(p)
        self.assertIsNone(row["ht_home"])
        self.assertIsNone(row["ht_away"])
        self.assertFalse(report["explicit_halftime_available"])

    def test_identity_full_score_and_halftime_contradictions_reject(self):
        for field, value in (("score", "3"), ("id", "999"),
                             ("linescores", [{"displayValue": "0"}, {"displayValue": "2"}])):
            p = copy.deepcopy(self.payload)
            p["header"]["competitions"][0]["competitors"][0][field] = value
            with self.assertRaises(ValueError):
                self.run_audit(p)

    def test_incomplete_events_do_not_overwrite_explicit_halftime(self):
        p = copy.deepcopy(self.payload)
        p["keyEvents"] = p["keyEvents"][:3]
        row, report = self.run_audit(p)
        self.assertEqual((row["ht_home"], row["ht_away"]), (1, 0))
        self.assertFalse(report["complete_goal_events_crosscheck"])


if __name__ == "__main__":
    unittest.main()
