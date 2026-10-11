"""Synthetic regression cases for archive integrity and post-match leakage."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from beidan_bd1.evaluation import _brier
from beidan_bd1.frozen_settlement import seal_bundle, settle_bundle
from test_beidan_l1_walk_forward import native_case, run


SEALED = datetime.fromisoformat("2026-10-07T10:00:02+08:00")
EVALUATED = "2026-10-09T23:00:00+08:00"


def make_bundle(path, blocked=False):
    history, folds, results = native_case()
    folds = folds[:1]
    if blocked:
        fixture = copy.deepcopy(folds[0]["fixtures"][0])
        fixture.update(match_id="blocked-1", fixture_source={"status": "missing"})
        folds[0]["fixtures"].append(fixture)
        folds[0]["expected_total"] = 2
    report = run(history, folds, [])
    # These are explicit synthetic test clocks, never real performance evidence.
    report["executed_at"] = "2026-10-07T10:00:01+08:00"
    report["prospective_freeze"] = {"generated_at": folds[0]["cutoff_at"]}
    for name, data in (("history.jsonl", history), ("results.jsonl", [])):
        (path / name).write_text("".join(json.dumps(row) + "\n" for row in data))
    (path / "folds.json").write_text(json.dumps(folds))
    (path / "report.json").write_text(json.dumps(report))
    result = results[0]
    result["result_source"] = {"status": "ok", "name": "synthetic_result", "raw_sha256": "0" * 64,
                               "available_at": result["verified_at"]}
    with patch("beidan_bd1.frozen_settlement._utc_now", return_value=SEALED):
        digest = seal_bundle(path)["manifest_sha256"]
    return report, result, digest


class FrozenSettlementTests(unittest.TestCase):
    def expire_blocked_row(self):
        folds=json.loads((self.path/'folds.json').read_bytes())
        folds[0]['fixtures'][1]['kickoff_at']='2026-10-07T09:00:00+08:00'
        self.report['folds'][0]['matches'][1]['kickoff_at']='2026-10-07T09:00:00+08:00'
        (self.path/'folds.json').write_text(json.dumps(folds))
        (self.path/'report.json').write_text(json.dumps(self.report))
        (self.path/'freeze_manifest.json').unlink()

    def test_expired_blocked_row_keeps_full_pool_without_creating_past_prediction(self):
        self.expire_blocked_row()
        with patch('beidan_bd1.frozen_settlement._utc_now',return_value=SEALED):
            sealed=seal_bundle(self.path)
        self.digest=sealed['manifest_sha256']
        self.assertEqual(sealed['expired_blocked_n'],1)
        out=self.settle([])
        self.assertEqual((out['offered_n'],out['predicted_n'],out['blocked_n']),(2,1,1))
        self.assertEqual(out['matches'][1]['status'],'blocked_in_original_freeze')

    def test_blocked_row_cannot_hide_a_probability_payload(self):
        self.expire_blocked_row()
        self.report['folds'][0]['matches'][1]['candidate']=self.report['folds'][0]['matches'][0]['candidate']
        (self.path/'report.json').write_text(json.dumps(self.report))
        with patch('beidan_bd1.frozen_settlement._utc_now',return_value=SEALED):
            with self.assertRaises(ValueError):seal_bundle(self.path)

    def test_legacy_protocol_retains_strict_whole_pool_time_rule(self):
        self.expire_blocked_row()
        with patch('beidan_bd1.frozen_settlement._utc_now',return_value=SEALED):seal_bundle(self.path)
        p=self.path/'freeze_manifest.json';manifest=json.loads(p.read_bytes())
        manifest['schema']='bd1-frozen-bundle-1';manifest.pop('expired_blocked_n')
        p.write_text(json.dumps(manifest));self.digest=hashlib.sha256(p.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):self.settle([])

    def test_expired_blocked_count_tampering_rejected_even_with_new_digest(self):
        self.expire_blocked_row()
        with patch('beidan_bd1.frozen_settlement._utc_now',return_value=SEALED):seal_bundle(self.path)
        p=self.path/'freeze_manifest.json';manifest=json.loads(p.read_bytes());manifest['expired_blocked_n']=0
        p.write_text(json.dumps(manifest));self.digest=hashlib.sha256(p.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):self.settle([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.report, self.result, self.digest = make_bundle(self.path, blocked=True)

    def settle(self, results=None, digest=None):
        return settle_bundle(self.path, manifest_sha256=digest or self.digest,
                             results=[self.result] if results is None else results, evaluated_at=EVALUATED)

    def test_saved_probability_scoring_never_refits_or_changes_frozen_bytes(self):
        before = {p.name: p.read_bytes() for p in self.path.iterdir()}
        with patch("beidan_bd1.walk_forward._team_candidate", side_effect=AssertionError("refit")), \
                patch("beidan_bd1.baseline.predict_l3", side_effect=AssertionError("refit")):
            out = self.settle()
        saved = self.report["folds"][0]["matches"][0]
        self.assertAlmostEqual(out["brier_candidate"], _brier(saved["candidate"]["vectors"]["wdl"], 0, 0))
        self.assertEqual((out["offered_n"], out["predicted_n"], out["paired_n"]), (2, 1, 1))
        self.assertEqual(out["prediction_coverage"], .5)
        self.assertFalse(out["refitted"])
        self.assertFalse(out["reselected_after_results"])
        self.assertFalse(out["production_gate_passed"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.path.iterdir()})

    def test_cold_start_none_is_not_replaced_with_best_post_match_ridge(self):
        out = self.settle()
        row = out["matches"][0]
        self.assertIsNone(row["selected_parameter"])
        self.assertEqual(row["baseline"], row["candidate"])
        self.assertEqual(set(row["declared_alternative_scores"]), {"None", "2.0", "5.0", "10.0", "20.0"})

    def test_missing_or_late_labels_stay_pending_and_do_not_get_fake_metrics(self):
        for results in ([], [{**self.result, "verified_at": "2026-10-10T21:00:00+08:00",
                             "result_source": {**self.result["result_source"], "available_at": "2026-10-10T21:00:00+08:00"}}]):
            out = self.settle(results)
            self.assertEqual(out["paired_n"], 0)
            self.assertEqual(out["pending_ids"], [self.result["match_id"]])
            self.assertIsNone(out["brier_candidate"])
            self.assertFalse(out["numerical_gate"]["numerical_nondegradation_gate"])

    def test_mutated_report_history_folds_and_original_results_rejected(self):
        for name in ("report.json", "history.jsonl", "folds.json", "results.jsonl"):
            path = self.path / name
            data = path.read_bytes()
            path.write_bytes(data + b" ")
            with self.assertRaises(ValueError): self.settle()
            path.write_bytes(data)

    def test_mutating_both_report_and_manifest_does_not_bypass_retained_digest(self):
        manifest = self.path / "freeze_manifest.json"
        data = json.loads(manifest.read_text())
        report = self.path / "report.json"
        report.write_bytes(report.read_bytes() + b" ")
        data["files"]["report.json"] = {"sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                                           "size_bytes": report.stat().st_size}
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.settle()

    def test_duplicates_unknown_ids_and_changed_native_identity_rejected(self):
        with self.assertRaises(ValueError): self.settle([self.result, self.result])
        for key, value in (("match_id", "unknown"), ("period", "wrong"),
                           ("provider_match_id", "other"), ("provider_home_id", "2"),
                           ("provider_league_id", "999"), ("season_type", 99),
                           ("kickoff_at", "2026-10-07T18:01:00+08:00")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.settle([{**self.result, key: value}])

    def test_result_provenance_and_explicit_half_validity_required(self):
        for changes in ({"result_source": None}, {"regular_time": False}, {"ft_home": True},
                        {"ht_home": 1}, {"ht_home": None}, {"summary_sha256": "1" * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.settle([{**self.result, **changes}])
        missing = {**self.result, "ht_home": None, "ht_away": None}
        self.assertIsNone(self.settle([missing])["matches"][0]["candidate"]["half_full_logloss"])

    def test_seal_cannot_overwrite_or_be_backdated_after_kickoff(self):
        with patch("beidan_bd1.frozen_settlement._utc_now", return_value=SEALED):
            with self.assertRaises(FileExistsError): seal_bundle(self.path)
        late = datetime.fromisoformat(self.result["kickoff_at"])
        with patch("beidan_bd1.frozen_settlement._utc_now", return_value=late):
            with self.assertRaises(ValueError): seal_bundle(self.path)

    def test_resealed_inconsistent_vectors_are_rejected_before_sealing(self):
        (self.path / "freeze_manifest.json").unlink()
        report = self.report
        value = report["folds"][0]["matches"][0]["alternatives"]["None"]
        value["vectors"]["wdl"] = {"胜": 1., "平": 0., "负": 0.}
        (self.path / "report.json").write_text(json.dumps(report))
        with patch("beidan_bd1.frozen_settlement._utc_now", return_value=SEALED):
            with self.assertRaises(ValueError): seal_bundle(self.path)

    def test_actual_completion_after_kickoff_is_not_a_forecast(self):
        (self.path / "freeze_manifest.json").unlink()
        self.report["executed_at"] = self.result["kickoff_at"]
        (self.path / "report.json").write_text(json.dumps(self.report))
        with self.assertRaises(ValueError): seal_bundle(self.path)

    def test_result_label_changes_scores_without_modifying_selected_parameter(self):
        before = self.settle()
        after = self.settle([{**self.result, "ft_home": 8}])
        self.assertNotEqual(before["brier_candidate"], after["brier_candidate"])
        self.assertEqual(before["matches"][0]["selected_parameter"], after["matches"][0]["selected_parameter"])


if __name__ == "__main__":
    unittest.main()
