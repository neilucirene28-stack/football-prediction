import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import urllib.error

from beidan_bd1.http_archive import capture_response


class HTTPArchiveTests(unittest.TestCase):
    def test_http_error_body_is_retained_without_retry(self):
        raw = b'Access denied\r\n'
        calls = []
        def opener(url, **kwargs):
            calls.append(url)
            raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, io.BytesIO(raw))
        with tempfile.TemporaryDirectory() as tmp:
            receipt, received = capture_response('https://example.invalid', tmp, 'denial.bin', opener=opener)
            self.assertEqual(calls, ['https://example.invalid'])
            self.assertEqual(received, raw)
            self.assertEqual((Path(tmp) / 'denial.bin').read_bytes(), raw)
            self.assertEqual(receipt['http_status'], 403)
            self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(receipt['bytes'], len(raw))

    def test_transport_failure_does_not_claim_received_bytes(self):
        def opener(*args, **kwargs):
            raise urllib.error.URLError('connection failed')
        with tempfile.TemporaryDirectory() as tmp:
            receipt, received = capture_response('https://example.invalid', tmp, 'missing.bin', opener=opener)
            self.assertIsNone(received)
            self.assertIn('connection failed', receipt['error'])
            self.assertNotIn('sha256', receipt)
            self.assertNotIn('bytes', receipt)
            self.assertFalse((Path(tmp) / 'missing.bin').exists())

    def test_existing_original_cannot_be_overwritten(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'new'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'original.bin'
            path.write_bytes(b'original')
            with self.assertRaises(FileExistsError):
                capture_response('https://example.invalid', tmp, path.name, opener=lambda *a, **kw: Response())
            self.assertEqual(path.read_bytes(), b'original')
