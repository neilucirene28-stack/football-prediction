import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from beidan_bd1.artifact_io import pack_artifact, read_artifact


class ArtifactIOTests(unittest.TestCase):
    def packed(self, tmp, compress=False):
        path = Path(tmp) / 'original.json'
        raw = b'{"raw":1}\r\n' * 30
        path.write_bytes(raw)
        pack_artifact(path, chunk_bytes=23, compress=compress)
        path.unlink()
        return path, raw

    def test_direct_bytes_remain_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'original.json'; path.write_bytes(b'original\r\n')
            self.assertEqual(read_artifact(path), b'original\r\n')

    def test_identity_and_gzip_parts_preserve_exact_original(self):
        for compress in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                path, raw = self.packed(tmp, compress)
                self.assertEqual(read_artifact(path), raw)

    def test_missing_part_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.packed(tmp)
            next(Path(str(path) + '.parts').glob('*.bin')).unlink()
            with self.assertRaises(FileNotFoundError): read_artifact(path)

    def test_stale_direct_copy_cannot_shadow_verified_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, raw = self.packed(tmp, True)
            path.write_bytes(b'incomplete restored intermediate')
            self.assertEqual(read_artifact(path), raw)

    def test_direct_copy_cannot_hide_corrupted_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, raw = self.packed(tmp)
            path.write_bytes(raw)
            next(Path(str(path) + '.parts').glob('*.bin')).write_bytes(b'tampered')
            with self.assertRaises(ValueError): read_artifact(path)

    def test_changed_part_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.packed(tmp)
            next(Path(str(path) + '.parts').glob('*.bin')).write_bytes(b'tampered')
            with self.assertRaises(ValueError): read_artifact(path)

    def test_original_digest_is_checked_after_decode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = self.packed(tmp, True)
            index_path = Path(str(path) + '.parts.json'); index = json.loads(index_path.read_bytes())
            index['original_sha256'] = hashlib.sha256(b'other').hexdigest()
            index_path.write_text(json.dumps(index))
            with self.assertRaisesRegex(ValueError, 'original'): read_artifact(path)

    def test_duplicate_or_traversal_parts_are_rejected(self):
        for traversal in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                path, _ = self.packed(tmp)
                index_path = Path(str(path) + '.parts.json'); index = json.loads(index_path.read_bytes())
                if traversal:index['parts'][0]['name'] = '../escape'
                else:index['parts'].append(index['parts'][0])
                index_path.write_text(json.dumps(index))
                with self.assertRaises(ValueError): read_artifact(path)
