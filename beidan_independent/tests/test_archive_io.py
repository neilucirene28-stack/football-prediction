import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from beidan_bd1.archive_io import read_response
from beidan_bd1.artifact_io import pack_artifact


class ArchiveIoTest(unittest.TestCase):
    def test_packed_response_preserves_original_bytes(self):
        raw = b'{"name":"FC","score":1}\r\n'
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with tarfile.open(folder/'original_responses.tar.gz', 'w:gz') as archive:
                info = tarfile.TarInfo('response.json'); info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            self.assertEqual(read_response(folder, 'response.json'), raw)

    def test_ambiguous_member_and_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with tarfile.open(folder/'original_responses.tar.gz', 'w:gz') as archive:
                for _ in range(2):
                    info = tarfile.TarInfo('response.json'); info.size = 2
                    archive.addfile(info, io.BytesIO(b'{}'))
            with self.assertRaises(ValueError): read_response(folder, 'response.json')
            with self.assertRaises(ValueError): read_response(folder, '../response.json')

    def test_stale_response_and_tar_copies_cannot_shadow_verified_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder/'original_responses.tar.gz'
            raw = b'original response\r\n'
            with tarfile.open(path, 'w:gz') as archive:
                info = tarfile.TarInfo('response.json'); info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            pack_artifact(path, chunk_bytes=23)
            path.write_bytes(b'partial gzip')
            (folder/'response.json').write_bytes(b'partial response')
            self.assertEqual(read_response(folder, 'response.json'), raw)
