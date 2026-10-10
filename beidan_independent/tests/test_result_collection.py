import unittest
import hashlib
import json
from pathlib import Path
import tempfile
import io
import urllib.error
from unittest.mock import patch
from beidan_bd1.result_collection import due_for_collection, collect


class CollectionDelayTests(unittest.TestCase):
    def collect_response(self, raw, *, status=200, http_error=False, pool=None, slug='test.1', scope=None):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return raw
        Response.status = status
        def opener(url, **kwargs):
            if http_error:
                raise urllib.error.HTTPError(url, status, 'failure', {}, io.BytesIO(raw))
            return Response()
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / 'audit.json'
            audit.write_text(json.dumps({'research_predictions': [{'binding': {
                'provider_league_id': '1', 'provider_league_slug': slug}}]}))
            report = {'identity_mode': 'provider_native_espn', 'prospective_freeze': {
                'history_audits_sha256': {str(audit): hashlib.sha256(audit.read_bytes()).hexdigest()}}}
            pool = pool or {'a': {'kickoff_at': '2026-10-09T00:00:00Z', 'provider_league_id': '1', 'provider_match_id': '2'}}
            out = Path(tmp) / 'out'
            with patch('beidan_bd1.result_collection.load_bundle', return_value=(report, pool, {k: {} for k in pool})), \
                 patch('beidan_bd1.result_collection.clock', return_value='2026-10-10T00:00:00Z'), \
                 patch('beidan_bd1.result_collection.settle_bundle', return_value={'paired_n': 0}) as settle:
                collect('bundle', 'digest', out, opener=opener, only_match_ids=scope)
            return json.loads((out / 'COLLECTION_INDEX.json').read_bytes()), \
                settle.call_args.kwargs['results'], (out / 'summary_2.json').read_bytes()

    def test_http_error_keeps_body_and_remains_pending(self):
        raw = b'Forbidden\r\n'
        index, results, saved = self.collect_response(raw, status=403, http_error=True)
        self.assertEqual(saved, raw)
        self.assertEqual(results, [])
        self.assertEqual(index['receipts'][0]['http_status'], 403)
        self.assertEqual(index['complete_pool_ledger'][0]['status'], 'pending_or_rejected')

    def test_scope_fetches_only_requested_event_without_losing_pool_ledger(self):
        pool = {'a': {'kickoff_at': '2026-10-09T00:00:00Z', 'provider_league_id': '1', 'provider_match_id': '2'},
                'b': {'kickoff_at': '2026-10-09T00:00:00Z', 'provider_league_id': '1', 'provider_match_id': '3'}}
        index, results, saved = self.collect_response(b'{bad JSON', pool=pool, scope=['a'])
        self.assertEqual(len(index['receipts']), 1)
        self.assertEqual(index['receipts'][0]['match_id'], 'a')
        self.assertEqual(index['collection_scope_match_ids'], ['a'])
        self.assertEqual(len(index['complete_pool_ledger']), 2)
        self.assertEqual(index['complete_pool_ledger'][1]['status'], 'outside_collection_scope')

    def test_invalid_scope_fails_before_http_or_output(self):
        for scope in ([], ['unknown'], ['a', 'a'], 'a'):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                self.collect_response(b'{}', scope=scope)

    def test_originally_blocked_match_cannot_be_requested(self):
        report = {'identity_mode': 'provider_native_espn'}
        pool = {'a': {}, 'b': {}}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            with patch('beidan_bd1.result_collection.load_bundle', return_value=(report, pool, {'a': {}})):
                with self.assertRaisesRegex(ValueError, 'originally blocked'):
                    collect('bundle', 'digest', out, only_match_ids=['b'])
            self.assertFalse(out.exists())

    def test_response_for_another_pool_event_is_rejected(self):
        raw = json.dumps({'header': {'id': '3'}}).encode()
        pool = {'a': {'kickoff_at': '2026-10-09T00:00:00Z', 'provider_league_id': '1', 'provider_match_id': '2'},
                'b': {'kickoff_at': '2026-10-11T00:00:00Z', 'provider_league_id': '1', 'provider_match_id': '3'}}
        index, results, saved = self.collect_response(raw, pool=pool)
        self.assertEqual(saved, raw)
        self.assertEqual(results, [])
        self.assertEqual(index['complete_pool_ledger'][0]['status'], 'pending_or_rejected')
        self.assertIn('唯一绑定', index['receipts'][0]['error'])
        self.assertEqual(index['complete_pool_ledger'][1]['status'], 'not_due')

    def test_success_status_with_wrong_slug_is_rejected(self):
        raw = json.dumps({'header': {'id': '2', 'league': {'id': '1', 'slug': 'other.1'},
                                   'competitions': [{'competitors': [{'homeAway': 'home'}, {'homeAway': 'away'}]}]}}).encode()
        index, results, saved = self.collect_response(raw)
        self.assertEqual(saved, raw)
        self.assertEqual(results, [])
        self.assertIn('slug', index['receipts'][0]['error'])

    def test_waits_for_expected_regular_time_end_without_claiming_ft(self):
        fixture = {'kickoff_at': '2026-10-10T00:30:00+08:00'}
        self.assertFalse(due_for_collection(fixture, '2026-10-09T18:14:59Z'))
        self.assertTrue(due_for_collection(fixture, '2026-10-09T18:15:00Z'))

    def test_timezone_free_clock_is_rejected(self):
        with self.assertRaises(ValueError):
            due_for_collection({'kickoff_at': '2026-10-10T00:30:00'}, '2026-10-09T18:15:00Z')

    def test_rejected_response_keeps_original_bytes_and_no_result(self):
        raw = b'{bad JSON\r\n'
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return raw
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / 'audit.json'
            audit.write_text(json.dumps({'research_predictions': [{'binding': {
                'provider_league_id': '1', 'provider_league_slug': 'test.1'}}]}))
            report = {'identity_mode': 'provider_native_espn', 'prospective_freeze': {
                'history_audits_sha256': {str(audit): hashlib.sha256(audit.read_bytes()).hexdigest()}}}
            pool = {'a': {'kickoff_at': '2026-10-09T00:00:00Z', 'provider_league_id': '1', 'provider_match_id': '2'}}
            out = Path(tmp) / 'out'
            with patch('beidan_bd1.result_collection.load_bundle', return_value=(report, pool, {'a': {}})), \
                 patch('beidan_bd1.result_collection.clock', return_value='2026-10-10T00:00:00Z'), \
                 patch('beidan_bd1.result_collection.settle_bundle', return_value={'paired_n': 0}) as settle:
                collect('bundle', 'digest', out, opener=lambda *a, **k: Response())
            self.assertEqual((out / 'summary_2.json').read_bytes(), raw)
            self.assertEqual((out / 'results.jsonl').read_bytes(), b'')
            self.assertEqual(settle.call_args.kwargs['results'], [])
            index = json.loads((out / 'COLLECTION_INDEX.json').read_bytes())
            self.assertEqual(index['receipts'][0]['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(index['complete_pool_ledger'][0]['status'], 'pending_or_rejected')
