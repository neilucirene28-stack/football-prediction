"""Evidence tests with explicit test clocks; mutations are not model results."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from beidan_bd1.archive_io import read_response
from beidan_bd1.native_batch_audit import audit_history

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data_sample/espn_eng.1_mined_20261010_v25'
START = '2026-10-10T02:00:00Z'
END = '2026-10-10T02:00:01Z'


class NativeBatchAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / 'raw'
        self.folder.mkdir()
        original = json.loads((SOURCE / 'VERIFIED_FILE_INDEX.json').read_bytes())
        schedule = next(r for r in original['files'] if r['kind'] == 'schedule' and r['team_id'] == '359')
        events = {e['id'] for e in json.loads(read_response(SOURCE, schedule['local_file']))['events']}
        self.index = {**original, 'files': [schedule] + [r for r in original['files']
              if r['kind'] == 'summary' and r['event_id'] in events], 'selected_summary_ids': sorted(events)}
        for entry in self.index['files']:
            (self.folder / entry['local_file']).write_bytes(read_response(SOURCE, entry['local_file']))
        self.config = {'folder': 'raw', 'league_slug': 'eng.1', 'season_year': 2026,
                       'season_type': 14308, 'team_ids': ['359']}

    def call(self):
        (self.folder / 'VERIFIED_FILE_INDEX.json').write_text(json.dumps(self.index))
        clocks = iter((START, END))
        return audit_history(self.config, self.root, now=lambda: next(clocks))

    def test_records_become_available_only_at_verification_completion(self):
        report = self.call()
        self.assertEqual((report['audited_n'], report['explicit_ht_n']), (5, 5))
        self.assertEqual(report['verification_started_at'], START)
        self.assertEqual(report['verified_at'], END)
        self.assertTrue(all(r['record']['verified_at'] == END for r in report['results']))

    def test_url_and_raw_body_tampering_rejected(self):
        self.index['files'] = copy.deepcopy(self.index['files'])
        self.index['files'][1]['url'] += '&other-event=1'
        with self.assertRaisesRegex(ValueError, 'URL differs'): self.call()
        self.index['files'][1]['url'] = self.index['files'][1]['url'].split('&')[0]
        path = self.folder / self.index['files'][1]['local_file']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'original differs'): self.call()

    def test_received_summary_cannot_be_another_valid_match(self):
        self.index['files'] = copy.deepcopy(self.index['files'])
        first, other = self.index['files'][1:3]
        raw = (self.folder / other['local_file']).read_bytes()
        (self.folder / first['local_file']).write_bytes(raw)
        first.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(ValueError, 'requested event ID'): self.call()

    def test_duplicate_receipts_and_missing_scope_rejected(self):
        self.index['files'].append(copy.deepcopy(self.index['files'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate history'): self.call()
        self.index['files'].pop()
        self.config['team_ids'].append('unknown')
        with self.assertRaisesRegex(ValueError, 'schedule scope'): self.call()

    def test_future_index_or_reversed_receipt_clock_rejected(self):
        original = self.index['created_at']
        self.index['created_at'] = '2099-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'not available'): self.call()
        self.index['created_at'] = original
        self.index['files'] = copy.deepcopy(self.index['files'])
        self.index['files'][0]['end_utc'] = '2026-10-09T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'clock order'): self.call()

    def test_failure_body_is_verified_and_stays_missing_history(self):
        first = copy.deepcopy(self.index['files'].pop(1))
        raw = b'HTTP gateway failure\n'
        (self.folder / first['local_file']).write_bytes(raw)
        first.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), http_status=502,
                     error='unsuccessful HTTP status')
        self.index['failed_requests'] = [first]
        report = self.call()
        self.assertEqual(report['audited_n'], 4)
        self.assertIn(first['event_id'], report['missing_summary_ids'])
        self.assertEqual(report['failed_http_requests_n'], 1)
        (self.folder / first['local_file']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'original differs'): self.call()


if __name__ == '__main__':
    unittest.main()
