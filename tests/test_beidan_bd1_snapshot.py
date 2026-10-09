"""BD-1 快照的可回测性与概率自洽性测试。"""
import json
import unittest

from beidan_bd1.snapshot import build_snapshot, save_snapshot


def case():
    return {
        "match_id": "26103-999", "period": "26103",
        "kickoff_at": "2026-10-10T15:30:00+08:00",
        "asof_at": "2026-10-09T14:20:00+08:00",
        "generated_at": "2026-10-09T14:21:00+08:00",
        "model_version": "bd1-shadow-test",
        "lambda_home": None, "lambda_away": None, "handicap": -1,
        "sources": [{"name": name, "source_match_id": "fixture-1",
                     "available_at": "2026-10-09T14:19:00+08:00", "status": "ok"}
                    for name in ("fixture_source", "history_source", "family_source", "handicap_source")],
        "input_sources": {"fixture": "fixture_source", "history": "history_source",
                          "family": "family_source", "handicap": "handicap_source"},
        "training_lineage": {"match_ids": ["26101:1"],
                             "latest_available_at": "2026-10-09T14:19:00+08:00"},
        "vectors": {
            "wdl": {"胜": .4, "平": .4, "负": .2},
            "handicap_wdl": {"胜": 0., "平": .4, "负": .6},
            "score": {"0-0": .2, "1-0": .4, "0-1": .2, "2-2": .2},
            "total_goals": {"0": .2, "1": .6, "2": 0., "3": 0.,
                            "4": .2, "5": 0., "6": 0., "7+": 0.},
            "half_full": {"胜胜": .4, "胜平": 0., "胜负": 0.,
                          "平胜": 0., "平平": .4, "平负": .2,
                          "负胜": 0., "负平": 0., "负负": 0.},
            "odd_even": {"上单": 0., "上双": .2, "下单": .6, "下双": .2},
        },
    }


class SnapshotTests(unittest.TestCase):
    def test_verified_snapshot_has_consistent_official_score_vector(self):
        import tempfile
        row = build_snapshot(**case())
        self.assertTrue(row["as_of_backtest_eligible"])
        self.assertFalse(row["observation_only"])
        self.assertEqual(len(row["score_31"]), 31)
        self.assertAlmostEqual(sum(row["score_31"].values()), 1)
        with tempfile.TemporaryDirectory() as folder:
            path = save_snapshot(folder, row)
            self.assertEqual(json.loads(path.read_text())["generated_at"], row["generated_at"])
            with self.assertRaises(FileExistsError):
                save_snapshot(folder, row)

    def test_unknown_source_time_cannot_enter_backtest(self):
        kw = case()
        kw["sources"][1]["available_at"] = None
        row = build_snapshot(**kw)
        self.assertTrue(row["provenance_unverified"])
        self.assertTrue(row["observation_only"])
        self.assertFalse(row["as_of_backtest_eligible"])

    def test_post_asof_source_and_post_kickoff_generation_rejected(self):
        kw = case()
        kw["sources"][0]["available_at"] = "2026-10-09T14:20:01+08:00"
        with self.assertRaisesRegex(ValueError, "尚不可得"):
            build_snapshot(**kw)
        kw = case()
        kw["generated_at"] = kw["kickoff_at"]
        with self.assertRaisesRegex(ValueError, "kickoff_at"):
            build_snapshot(**kw)

    def test_mismatched_score_derived_vectors_rejected(self):
        for key, entry, wrong in [("wdl", "胜", .5),
                                  ("handicap_wdl", "平", .5),
                                  ("total_goals", "4", .3),
                                  ("odd_even", "上双", .3),
                                  ("half_full", "平平", .5)]:
            kw = case()
            kw["vectors"][key][entry] = wrong
            with self.assertRaises(ValueError):
                build_snapshot(**kw)

    def test_reject_sensitive_extra_source_fields(self):
        kw = case()
        kw["sources"][0]["api_key"] = "DO-NOT-STORE"
        with self.assertRaisesRegex(ValueError, "白名单"):
            build_snapshot(**kw)

    def test_unknown_handicap_is_not_zero(self):
        kw = case()
        kw["handicap"] = None
        kw["vectors"]["handicap_wdl"] = None
        row = build_snapshot(**kw)
        self.assertIsNone(row["vectors"]["handicap_wdl"])

    def test_handicap_evidence_cannot_be_replaced_by_unrelated_source(self):
        kw = case()
        kw["sources"][3]["available_at"] = None
        row = build_snapshot(**kw)
        self.assertTrue(row["observation_only"])
        self.assertFalse(row["as_of_backtest_eligible"])

        kw = case()
        del kw["input_sources"]["handicap"]
        with self.assertRaisesRegex(ValueError, "来源绑定"):
            build_snapshot(**kw)

    def test_legacy_or_incorrect_binding_never_promotes_to_asof(self):
        kw = case()
        del kw["input_sources"]
        self.assertTrue(build_snapshot(**kw)["observation_only"])
        kw = case()
        kw["input_sources"]["handicap"] = "not_a_source"
        with self.assertRaisesRegex(ValueError, "来源不存在"):
            build_snapshot(**kw)

    def test_synthetic_clock_override_never_promotes_to_backtest(self):
        kw = case()
        kw["synthetic_sample"] = True
        row = build_snapshot(**kw)
        self.assertTrue(row["observation_only"])
        self.assertFalse(row["as_of_backtest_eligible"])

    def test_training_lineage_is_required_and_must_match_source_time(self):
        kw = case()
        del kw["training_lineage"]
        self.assertTrue(build_snapshot(**kw)["observation_only"])
        kw = case()
        kw["training_lineage"]["latest_available_at"] = "2026-10-09T14:20:30+08:00"
        with self.assertRaisesRegex(ValueError, "尚不可得"):
            build_snapshot(**kw)
        kw = case()
        kw["training_lineage"]["latest_available_at"] = "2026-10-09T14:18:00+08:00"
        with self.assertRaisesRegex(ValueError, "血缘不一致"):
            build_snapshot(**kw)


if __name__ == "__main__":
    unittest.main()
