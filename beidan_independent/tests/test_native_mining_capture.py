import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from scripts.mine_espn_native import main


class MiningCaptureTests(unittest.TestCase):
    def run_mining(self, tmp, opener, *, resume=False):
        config = Path(tmp) / 'config.json'
        folder = Path(tmp) / 'responses'
        config.write_text(json.dumps({'folder': str(folder), 'league_slug': 'test.1',
                                      'season_year': 2026, 'season_type': 1,
                                      'team_ids': ['1'], 'recent_per_team': 6}))
        args = ['mine_espn_native', str(config)] + (['--resume'] if resume else [])
        with patch('sys.argv', args), contextlib.redirect_stdout(io.StringIO()):
            main(opener=opener)
        return folder, json.loads((folder / 'VERIFIED_FILE_INDEX.json').read_bytes())

    def test_malformed_success_payload_is_saved_before_parse(self):
        raw = b'{not JSON\r\n'
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return raw
        with tempfile.TemporaryDirectory() as tmp:
            folder, index = self.run_mining(tmp, lambda *a, **kw: Response())
            receipt = index['failed_requests'][0]
            self.assertEqual((folder / receipt['local_file']).read_bytes(), raw)
            self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(index['files'], [])
            self.assertEqual(index['selected_summary_ids'], [])
            # An explicit later attempt keeps both received originals.
            _, resumed = self.run_mining(tmp, lambda *a, **kw: Response(), resume=True)
            self.assertNotEqual(resumed['failed_requests'][0]['local_file'], receipt['local_file'])
            self.assertEqual((folder / receipt['local_file']).read_bytes(), raw)

    def test_http_denial_keeps_body_and_never_requests_summaries(self):
        calls = []
        raw = b'Access denied\r\n'
        def opener(url, **kwargs):
            calls.append(url)
            raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, io.BytesIO(raw))
        with tempfile.TemporaryDirectory() as tmp:
            folder, index = self.run_mining(tmp, opener)
            self.assertEqual(len(calls), 1)
            receipt = index['failed_requests'][0]
            self.assertEqual(receipt['http_status'], 403)
            self.assertEqual((folder / receipt['local_file']).read_bytes(), raw)
            self.assertEqual(index['selected_summary_ids'], [])

    def test_rejected_schedule_keeps_receipt_index(self):
        raw = b'{}'
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return raw
        with tempfile.TemporaryDirectory() as tmp:
            folder, index = self.run_mining(tmp, lambda *a, **kw: Response())
            self.assertEqual(len(index['files']), 1)
            self.assertEqual(len(index['rejected_schedules']), 1)
            self.assertEqual(index['selected_summary_ids'], [])
            self.assertEqual((folder / index['files'][0]['local_file']).read_bytes(), raw)
