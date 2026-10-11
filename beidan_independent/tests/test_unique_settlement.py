"""Synthetic tests; these results are not real football performance evidence."""
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from beidan_bd1.frozen_settlement import seal_bundle, settle_bundle
from beidan_bd1.unique_settlement import settle_unique
from test_beidan_frozen_settlement import make_bundle, EVALUATED

spec = importlib.util.spec_from_file_location('register', Path(__file__).resolve().parents[1] / 'scripts/register_prospective_freezes.py')
register = importlib.util.module_from_spec(spec)
spec.loader.exec_module(register)


class UniqueSettlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.early, self.late = self.root/'early', self.root/'late'
        self.early.mkdir(); self.late.mkdir()
        _, self.result, self.early_digest = make_bundle(self.early, blocked=True)
        late_report, _, _ = make_bundle(self.late, blocked=True)
        # Simulate a later prospectively sealed version with another preselected parameter.
        late_report['executed_at'] = '2026-10-07T10:00:03+08:00'
        saved = late_report['folds'][0]['matches'][0]
        saved['selected_parameter'] = 5.0
        saved['candidate'] = copy.deepcopy(saved['alternatives']['5.0'])
        (self.late/'report.json').write_text(json.dumps(late_report))
        (self.late/'freeze_manifest.json').unlink()
        with patch('beidan_bd1.frozen_settlement._utc_now', return_value=datetime.fromisoformat('2026-10-07T10:00:04+08:00')):
            self.late_digest = seal_bundle(self.late)['manifest_sha256']
        self.registry = register.build([(str(self.late), self.late_digest), (str(self.early), self.early_digest)])
        self.registry_path = self.root/'registry.json'
        self.write_registry()

    def write_registry(self):
        self.registry_path.write_text(json.dumps(self.registry))
        self.registry_digest = hashlib.sha256(self.registry_path.read_bytes()).hexdigest()

    def call(self, results=None):
        return settle_unique(self.registry_path, registry_sha256=self.registry_digest,
                             results=[self.result] if results is None else results, evaluated_at=EVALUATED)

    def test_scores_earliest_probability_once_and_retains_complete_pool(self):
        before = {str(p):p.read_bytes() for folder in (self.early,self.late) for p in folder.iterdir()}
        expected = settle_bundle(self.early, manifest_sha256=self.early_digest, results=[self.result], evaluated_at=EVALUATED)
        with patch('beidan_bd1.walk_forward._team_candidate', side_effect=AssertionError('refit')):
            out = self.call()
        self.assertEqual((out['offered_n'],out['predicted_n'],out['paired_n']), (2,1,1))
        self.assertEqual(out['later_duplicate_predictions_excluded_n'],1)
        self.assertEqual(out['brier_candidate'],expected['brier_candidate'])
        settled = next(r for r in out['matches'] if r['status']=='settled_frozen_probabilities')
        self.assertIsNone(settled['selected_parameter'])
        self.assertEqual(settled['original_manifest_sha256'],self.early_digest)
        self.assertFalse(out['production_gate_passed'])
        self.assertEqual(before, {str(p):p.read_bytes() for folder in (self.early,self.late) for p in folder.iterdir()})

    def test_registry_cannot_select_later_version_even_with_matching_new_digest(self):
        row = self.registry['rows'][0]
        row.update(bundle=str(self.late), manifest_sha256=self.late_digest,
                   sealed_at='2026-10-07T10:00:04+08:00')
        self.write_registry()
        with self.assertRaises(ValueError):self.call()

    def test_duplicate_or_unpredicted_results_rejected(self):
        with self.assertRaises(ValueError):self.call([self.result,copy.deepcopy(self.result)])
        with self.assertRaises(ValueError):self.call([{**self.result,'match_id':'blocked-1'}])

    def test_missing_results_stay_pending_with_null_brier(self):
        out = self.call([])
        self.assertEqual(out['paired_n'],0)
        self.assertIsNone(out['brier_candidate'])
        self.assertEqual(len(out['pending_ids']),1)

    def test_registry_digest_and_metadata_tampering_rejected(self):
        with self.assertRaises(ValueError):
            settle_unique(self.registry_path,registry_sha256='0'*64,results=[],evaluated_at=EVALUATED)
        self.registry['bundles'][0]['predicted_n'] += 1
        self.write_registry()
        with self.assertRaises(ValueError):self.call()
