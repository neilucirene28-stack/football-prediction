"""Synthetic probability and clock regressions, never real model evidence."""
import copy
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from beidan_bd1.calibration_review import summarize_probabilities, review_calibration
from beidan_bd1.frozen_settlement import seal_bundle
from test_beidan_frozen_settlement import make_bundle, EVALUATED

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CalibrationMetricTests(unittest.TestCase):
    def rows(self):
        return [{'period': 'p1', 'match_id': 'a', 'probabilities': {'A': .8, 'B': .2}, 'actual': 'A'},
                {'period': 'p1', 'match_id': 'b', 'probabilities': {'A': .4, 'B': .6}, 'actual': 'A'}]

    def test_proper_scores_and_fixed_bin_errors_match_hand_calculation(self):
        value = summarize_probabilities(self.rows(), labels=('A', 'B'))
        self.assertAlmostEqual(value['brier_category_mean'], .2)
        self.assertAlmostEqual(value['natural_logloss'], -(math.log(.8) + math.log(.4)) / 2)
        self.assertAlmostEqual(value['top_label_ece'], .4)
        self.assertAlmostEqual(value['mean_classwise_ece'], .4)
        self.assertAlmostEqual(value['confidence_minus_accuracy'], .2)

    def test_empty_sample_and_empty_bins_do_not_invent_empirical_rates(self):
        value = summarize_probabilities([], labels=('A', 'B'))
        for key in ('brier_category_mean', 'natural_logloss', 'accuracy', 'top_label_ece'):
            self.assertIsNone(value[key])
        for b in value['top_label_bins']:
            self.assertIsNone(b['empirical_rate'])
            self.assertIsNone(b['wilson_95'])

    def test_exact_decimal_boundary_and_one_have_declared_bins(self):
        rows = self.rows()
        rows[0]['probabilities'] = {'A': .3, 'B': .7}
        rows[1]['probabilities'] = {'A': 1., 'B': 0.}
        value = summarize_probabilities(rows, labels=('A', 'B'))
        self.assertEqual(value['classwise_bins']['A'][3]['n'], 1)
        self.assertEqual(value['classwise_bins']['A'][9]['n'], 1)
        self.assertTrue(value['classwise_bins']['A'][9]['upper_inclusive'])

    def test_zero_actual_probability_is_infinite_loss_without_hidden_floor(self):
        rows = self.rows()[:1]
        rows[0]['probabilities'] = {'A': 0., 'B': 1.}
        value = summarize_probabilities(rows, labels=('A', 'B'))
        self.assertTrue(value['infinite_logloss'])
        self.assertIsNone(value['natural_logloss'])
        self.assertEqual(value['zero_actual_probability_n'], 1)
        self.assertFalse(value['probability_floor_applied'])
        json.dumps(value, allow_nan=False)

    def test_invalid_vectors_are_rejected_without_renormalization(self):
        for probabilities in ({'A': math.nan, 'B': 1.}, {'A': True, 'B': 0.},
                              {'A': -.1, 'B': 1.1}, {'A': .7, 'B': .7}, {'A': 1.}):
            rows = self.rows()[:1]
            rows[0]['probabilities'] = probabilities
            with self.assertRaises(ValueError): summarize_probabilities(rows, labels=('A', 'B'))

    def test_duplicate_identity_rejected_but_distinct_periods_are_distinct(self):
        row = self.rows()[0]
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            summarize_probabilities([row, copy.deepcopy(row)], labels=('A', 'B'))
        self.assertEqual(summarize_probabilities([row, {**row, 'period': 'p2'}], labels=('A', 'B'))['n'], 2)


class CalibrationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.early, self.late = self.root/'early', self.root/'late'
        self.early.mkdir(); self.late.mkdir()
        self.report, self.result, early_sha = make_bundle(self.early, blocked=True)
        late, _, _ = make_bundle(self.late, blocked=True)
        late['executed_at'] = '2026-10-07T10:00:03+08:00'
        saved = late['folds'][0]['matches'][0]
        saved['selected_parameter'] = 5.
        saved['candidate'] = copy.deepcopy(saved['alternatives']['5.0'])
        (self.late/'report.json').write_text(json.dumps(late))
        (self.late/'freeze_manifest.json').unlink()
        with patch('beidan_bd1.frozen_settlement._utc_now', return_value=datetime.fromisoformat('2026-10-07T10:00:04+08:00')):
            late_sha = seal_bundle(self.late)['manifest_sha256']
        self.registry = self.root/'registry.json'
        self.registry.write_text(json.dumps(load_script('register_prospective_freezes').build(
            [(str(self.late), late_sha), (str(self.early), early_sha)])))
        self.digest = hashlib.sha256(self.registry.read_bytes()).hexdigest()
        self.results = self.root/'results.jsonl'

    def call(self, results):
        self.results.write_text(''.join(json.dumps(r) + '\n' for r in results))
        return review_calibration(self.registry, self.digest, self.results, evaluated_at=EVALUATED, root=self.root)

    def test_earliest_versions_only_without_fitting_or_archive_mutation(self):
        before = {str(p): p.read_bytes() for folder in (self.early, self.late) for p in folder.iterdir()}
        with patch('beidan_bd1.walk_forward._team_candidate', side_effect=AssertionError('refit')):
            value = self.call([self.result])
        self.assertEqual(value['paired_n'], 1)
        self.assertEqual(value['original_forecast_provenance'][0]['bundle'], str(self.early))
        self.assertIsNone(value['original_forecast_provenance'][0]['selected_parameter'])
        self.assertFalse(value['minimum_sample_gate']['sample_threshold_met'])
        self.assertFalse(value['refitted'])
        self.assertEqual(before, {str(p): p.read_bytes() for folder in (self.early, self.late) for p in folder.iterdir()})

    def test_missing_halftime_only_excludes_halftime_markets(self):
        value = self.call([{**self.result, 'ht_home': None, 'ht_away': None}])
        for model in value['metrics'].values():
            self.assertEqual(model['wdl']['n'], 1)
            self.assertEqual(model['half_full']['n'], 0)
            self.assertEqual(model['half_wdl']['n'], 0)
            self.assertIsNone(model['half_full']['brier_category_mean'])

    def test_future_result_and_empty_results_stay_unscored_in_both_reviews(self):
        future = {**self.result, 'verified_at': '2026-10-10T21:00:00+08:00',
                  'result_source': {**self.result['result_source'], 'available_at': '2026-10-10T21:00:00+08:00'}}
        for i, results in enumerate(([future], [])):
            value = self.call(results)
            self.assertEqual(value['paired_n'], 0)
            self.assertEqual(value['metrics']['selected'], {})
            self.assertEqual(value['excluded_not_yet_available_result_n'], len(results))
            stats = load_script('review_frozen_results').review(self.registry, self.digest, self.results,
                self.root/f'review{i}.json', self.root/f'review{i}.md', evaluated_at=EVALUATED)
            self.assertEqual(stats['all']['n'], 0)
            self.assertIsNone(stats['all']['selected_brier'])

    def test_duplicate_results_and_changed_registry_digest_are_rejected(self):
        with self.assertRaises(ValueError): self.call([self.result, self.result])
        self.digest = '0' * 64
        with self.assertRaises(ValueError): self.call([self.result])
