"""Audited identity joins never infer canonical IDs from a shared name."""
import copy
import unittest

from beidan_bd1.identity_import import attach_identities, payload_digest


def example(kind="history"):
    payload = {"home": "甲", "away": "乙", "league": "杯赛",
               "kickoff_at": "2026-09-04T18:30:00+08:00",
               "home_id": "provider-11", "away_id": "provider-22",
               "competition_id": "provider-99"}
    evidence = {"match_id": "26092:1" if kind == "history" else "26103-1",
                "source_name": "archived_provider", "source_match_id": "match-33",
                "collected_at": "2026-10-09T13:00:00+08:00" if kind == "history"
                else "2026-09-04T14:00:00+08:00",
                "audited_at": "2026-10-09T14:00:00+08:00" if kind == "history"
                else "2026-09-04T15:00:00+08:00",
                "auditor_id": "human-1", "audit_status": "approved",
                "payload": payload, "payload_sha256": payload_digest(payload)}
    target = {"match_id": evidence["match_id"], "kickoff_at": payload["kickoff_at"]}
    target.update({("observed_home" if kind == "history" else "home"): "甲",
                   ("observed_away" if kind == "history" else "away"): "乙",
                   ("observed_competition" if kind == "history" else "league"): "杯赛"})
    return target, evidence


class IdentityImportTests(unittest.TestCase):
    def test_retrospective_history_identity_is_forward_only(self):
        target, evidence = example()
        rows, report = attach_identities([target], [evidence], kind="history")
        self.assertEqual(report["audited_identity_n"], 1)
        self.assertEqual(rows[0]["home_id"], "provider-11")
        self.assertEqual(rows[0]["identity_verified_at"], "2026-10-09T14:00:00+08:00")
        self.assertNotIn("home_id", target)

    def test_exact_names_kickoff_and_hash_guard_against_bad_join(self):
        target, evidence = example()
        for altered in ({**target, "observed_home": "丙"},
                        {**target, "kickoff_at": "2026-09-05T18:30:00+08:00"}):
            with self.assertRaisesRegex(ValueError, "不一致"):
                attach_identities([altered], [evidence], kind="history")
        bad = copy.deepcopy(evidence)
        bad["payload"]["home_id"] = "provider-44"
        with self.assertRaisesRegex(ValueError, "摘要"):
            attach_identities([target], [bad], kind="history")

    def test_fixture_needs_pre_kickoff_audit_and_attaches_source_bindings(self):
        target, evidence = example("fixtures")
        out, _ = attach_identities([target], [evidence], kind="fixtures")
        self.assertEqual(out[0]["input_sources"]["home_id"], "archived_provider")
        self.assertEqual(out[0]["sources"][0]["available_at"], evidence["audited_at"])
        late = copy.deepcopy(evidence)
        late["audited_at"] = "2026-09-04T19:00:00+08:00"
        with self.assertRaisesRegex(ValueError, "开球前"):
            attach_identities([target], [late], kind="fixtures")


if __name__ == "__main__":
    unittest.main()
