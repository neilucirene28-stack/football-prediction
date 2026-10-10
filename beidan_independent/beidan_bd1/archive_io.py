"""Read exact response bytes from a file or a losslessly packed raw archive."""
from pathlib import Path
import io
import tarfile
from .artifact_io import read_artifact


def read_response(folder, filename):
    folder = Path(folder)
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError('response filename must be a plain archive member name')
    path = folder / filename
    archive_path = folder / 'original_responses.tar.gz'
    if path.exists() and not Path(str(archive_path) + '.parts.json').exists():
        return path.read_bytes()
    packed = read_artifact(archive_path)
    with tarfile.open(fileobj=io.BytesIO(packed), mode='r:gz') as archive:
        members = [m for m in archive.getmembers() if m.name == filename]
        if len(members) != 1 or not members[0].isfile():
            raise ValueError('raw archive member is absent, duplicated or not a file')
        with archive.extractfile(members[0]) as response:
            return response.read()
