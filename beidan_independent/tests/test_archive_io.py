import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from beidan_bd1.archive_io import read_response


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
