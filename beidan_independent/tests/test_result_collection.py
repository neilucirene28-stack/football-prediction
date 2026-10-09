import unittest
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from beidan_bd1.result_collection import due_for_collection, collect


class CollectionDelayTests(unittest.TestCase):
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
