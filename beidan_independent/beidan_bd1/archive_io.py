"""Read exact response bytes from a file or a losslessly packed raw archive."""
from pathlib import Path
import tarfile


def read_response(folder, filename):
    folder = Path(folder)
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError('response filename must be a plain archive member name')
    path = folder / filename
    if path.exists():
        return path.read_bytes()
    with tarfile.open(folder / 'original_responses.tar.gz', 'r:gz') as archive:
        members = [m for m in archive.getmembers() if m.name == filename]
        if len(members) != 1 or not members[0].isfile():
            raise ValueError('raw archive member is absent, duplicated or not a file')
        with archive.extractfile(members[0]) as response:
            return response.read()
