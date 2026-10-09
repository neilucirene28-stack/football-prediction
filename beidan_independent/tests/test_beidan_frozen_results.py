import copy
import hashlib
import json
from pathlib import Path
import unittest

from beidan_bd1.espn_schedule import parse_schedule
from beidan_bd1.frozen_results import import_espn_result


class FrozenResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "data_sample"
        cls.raw = (root / "espn_j1_dd2deb7/espn_summary_401877614.json").read_bytes()
        cls.payload = json.loads(cls.raw)
        raw = json.loads((root / "espn_j1_9b4f6b8/espn_team_7476.json").read_text())
        rows, _ = parse_schedule(raw, expected_team_id="7476", verified_at="2026-10-09T10:00:00Z")
        fixture = next(r for r in rows if r["provider_match_id"] == "401877614")
        fixture = {**fixture, "period": "synthetic-import", "match_id": "synthetic-1"}
        for field in ("ft_home", "ft_away", "ht_home", "ht_away"):
            fixture.pop(field, None)
        cls.pool = {fixture["match_id"]: fixture}

    def call(self, payload=None, pool=None):
        raw = self.raw if payload is None else json.dumps(payload).encode()
        return import_espn_result(raw, pool=pool or self.pool, verified_at="2026-10-09T10:00:00Z")

    def test_real_summary_binds_ids_and_keeps_new_verification_clock(self):
        r = self.call()
        self.assertEqual([r[k] for k in ("ft_home", "ft_away", "ht_home", "ht_away")], [2, 4, 1, 0])
        self.assertEqual(r["verified_at"], "2026-10-09T10:00:00Z")
        self.assertEqual(r["result_source"]["raw_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertFalse(r["full_chain_eligible"])

    def test_live_extra_time_and_wrong_league_are_rejected(self):
        for status in ("STATUS_IN_PROGRESS", "STATUS_FINAL_AET"):
            p = copy.deepcopy(self.payload)
            p["header"]["competitions"][0]["status"]["type"]["name"] = status
            with self.assertRaises(ValueError): self.call(p)
        p = copy.deepcopy(self.payload); p["header"]["league"]["id"] = "999"
        with self.assertRaises(ValueError): self.call(p)

    def test_unknown_or_multiple_bindings_are_rejected(self):
        p = copy.deepcopy(self.payload); p["header"]["id"] = "unknown"
        with self.assertRaises(ValueError): self.call(p)
        pool = {**self.pool, "duplicate": copy.deepcopy(next(iter(self.pool.values())))}
        with self.assertRaises(ValueError): self.call(pool=pool)

    def test_one_missing_half_does_not_invent_the_other_half(self):
        p = copy.deepcopy(self.payload)
        side = p["header"]["competitions"][0]["competitors"][0]
        side.pop("linescores")
        r = self.call(p)
        self.assertIsNone(r["ht_home"])
        self.assertIsNone(r["ht_away"])
        self.assertIsNone(r["explicit_halftime_fields"][side["homeAway"]])
        self.assertFalse(r["result_audit"]["explicit_halftime_available"])


if __name__ == "__main__":
    unittest.main()
