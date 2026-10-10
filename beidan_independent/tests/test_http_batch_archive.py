import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from beidan_bd1.archive_io import read_response
from beidan_bd1.http_batch_archive import archive_http_batch


class HttpBatchArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.bodies = {'ok.json': b'{"score":1}\r\n', 'failed.json': b'502\n\x00\xff'}
        entries = []
        for name, raw in self.bodies.items():
            (self.folder/name).write_bytes(raw)
            entries.append({'local_file': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                            'http_status': 200 if name == 'ok.json' else 502})
        self.index = {'files': entries[:1], 'failed_requests': entries[1:] + [{'error': 'no received response'}]}

    def call(self):
        (self.folder/'VERIFIED_FILE_INDEX.json').write_text(json.dumps(self.index))
        return archive_http_batch(self.folder, chunk_bytes=23)

    def test_exact_bytes_and_failed_body_survive_parts(self):
        result = self.call()
        self.assertEqual((result['archived_body_n'], result['receipts_n'], result['failed_http_n']), (2, 3, 2))
        for name, raw in self.bodies.items():
            self.assertFalse((self.folder/name).exists())
            self.assertEqual(read_response(self.folder, name), raw)

    def test_corrupted_original_is_rejected_without_removal(self):
        (self.folder/'failed.json').write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'before archiving'): self.call()
        self.assertTrue((self.folder/'ok.json').exists())
        self.assertFalse((self.folder/'original_responses.tar.gz').exists())

    def test_duplicate_or_unsafe_member_is_rejected(self):
        self.index['files'].append(self.index['files'][0])
        with self.assertRaisesRegex(ValueError, 'duplicate'): self.call()
        self.index['files'].pop()
        self.index['files'][0]['local_file'] = '../ok.json'
        with self.assertRaisesRegex(ValueError, 'unsafe'): self.call()

    def test_existing_evidence_cannot_be_overwritten(self):
        (self.folder/'original_responses.tar.gz.parts').mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'): self.call()
        self.assertTrue((self.folder/'ok.json').exists())
