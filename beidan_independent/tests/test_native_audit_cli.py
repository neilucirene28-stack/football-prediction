"""Receipt integrity must survive optimized Python execution."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class NativeAuditCliTest(unittest.TestCase):
    def test_optimized_execution_rejects_tampered_original_response(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'raw.json').write_bytes(b'{}')
            (folder/'VERIFIED_FILE_INDEX.json').write_text(json.dumps({
                'created_at': '2026-01-01T00:00:02Z', 'failed_requests': [],
                'selected_summary_ids': [], 'files': [{
                'local_file': 'raw.json', 'sha256': '0'*64, 'bytes': 2,
                'url': 'https://site.api.espn.com/apis/site/v2/sports/soccer/jpn.1/teams/1/schedule',
                'http_status': 200, 'start_utc': '2026-01-01T00:00:00Z',
                'end_utc': '2026-01-01T00:00:01Z', 'kind': 'schedule', 'team_id': '1'}]}))
            config = folder/'config.json'
            config.write_text(json.dumps({'folder': str(folder), 'league_slug': 'jpn.1',
                                         'season_year': 2026, 'season_type': 14287, 'team_ids': ['1']}))
            result = subprocess.run([sys.executable, '-O', 'scripts/audit_native_batch.py', str(config)],
                    cwd=root, env={**os.environ, 'PYTHONPATH': str(root)},
                    capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('history original differs from HTTP receipt', result.stderr)
