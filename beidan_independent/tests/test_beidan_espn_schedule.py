import copy
import json
from pathlib import Path
import unittest

from beidan_bd1.espn_schedule import parse_schedule, deduplicate


def fixture():
    # Small structural fixture, not an external-data credibility assertion.
    competitors = [{"id": i, "type": "team", "homeAway": side,
                    "team": {"id": i, "displayName": name}, "score": {"value": score}}
                   for i, side, name, score in (("1", "home", "Home", 2), ("2", "away", "Away", 1))]
    c = {"id": "100", "date": "2026-09-01T10:00Z", "competitors": competitors,
         "status": {"period": 2, "type": {"name": "STATUS_FULL_TIME", "completed": True, "state": "post"}}}
    e = {"id": "100", "date": c["date"], "season": {"year": 2026},
         "seasonType": {"type": 14287}, "league": {"slug": "jpn.1", "id": "750"}, "competitions": [c]}
    return {"team": {"id": "1"}, "events": [e]}


def parse(p):
    return parse_schedule(p, expected_team_id="1", verified_at="2026-10-09T17:00:00+08:00")


class EspnScheduleTests(unittest.TestCase):
    def test_halftime_is_missing_and_no_identity_is_fabricated(self):
        rows, report = parse(fixture())
        self.assertEqual(report["kept_n"], 1)
        self.assertIsNone(rows[0]["ht_home"])
        self.assertFalse(rows[0]["identity_verified"])
        self.assertFalse(rows[0]["full_chain_eligible"])

    def test_penalties_and_other_stages_are_not_mixed(self):
        p = fixture()
        other = copy.deepcopy(p["events"][0]); other["seasonType"]["type"] = 999
        pen = copy.deepcopy(p["events"][0]); pen["competitions"][0]["status"]["period"] = 5
        pen["competitions"][0]["status"]["type"]["name"] = "STATUS_FINAL_PEN"
        p["events"].extend((other, pen))
        rows, report = parse(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(sum(report["skipped"].values()), 2)

    def test_duplicate_observations_do_not_double_count_matches(self):
        rows, _ = parse(fixture())
        self.assertEqual(len(deduplicate(rows + rows)), 1)
        changed = copy.deepcopy(rows[0]); changed["ft_home"] = 3
        with self.assertRaisesRegex(ValueError, "矛盾"):
            deduplicate(rows + [changed])

    def test_bad_query_and_ambiguous_homeaway_are_rejected(self):
        p = fixture(); p["team"]["id"] = "9"
        with self.assertRaises(ValueError): parse(p)
        p = fixture(); p["events"][0]["competitions"][0]["competitors"][1]["homeAway"] = "home"
        with self.assertRaises(ValueError): parse(p)


if __name__ == "__main__":
    unittest.main()
