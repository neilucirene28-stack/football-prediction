"""Losslessly archive receipt bodies, verifying parts before removing duplicates."""
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
from .artifact_io import pack_artifact, read_artifact


def archive_http_batch(folder, *, index_name='VERIFIED_FILE_INDEX.json', chunk_bytes=48000):
    folder = Path(folder)
    code_sha256 = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                   for name in ('http_batch_archive.py', 'artifact_io.py', 'archive_io.py')}
    if type(chunk_bytes) is not int or chunk_bytes < 1:
        raise ValueError('chunk size must be a positive integer')
    if (folder / 'RAW_ARCHIVE_AUDIT.json').exists():
        raise ValueError('HTTP archive audit already exists; original evidence cannot be replaced')
    index_raw = (folder / index_name).read_bytes()
    index = json.loads(index_raw)
    receipts = index['files'] + index.get('failed_requests', [])
    bodies = {}
    for entry in receipts:
        name = entry.get('local_file')
        if name is None:
            continue  # A failed connection may have received no body.
        if not isinstance(name, str) or Path(name).name != name or name in bodies:
            raise ValueError('unsafe or duplicate HTTP archive member')
        raw = (folder / name).read_bytes()
        if type(entry['bytes']) is not int or len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('HTTP body differs from receipt before archiving')
        bodies[name] = raw
    if not bodies:
        raise ValueError('HTTP batch has no received bodies to archive')
    path = folder / 'original_responses.tar.gz'
    if path.exists() or Path(str(path) + '.parts.json').exists() or Path(str(path) + '.parts').exists():
        raise ValueError('HTTP archive already exists; original evidence cannot be replaced')
    with path.open('xb') as output, gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode='w') as archive:
            for name, raw in sorted(bodies.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(raw), 0o644, 0
                archive.addfile(info, io.BytesIO(raw))
    parts = pack_artifact(path, chunk_bytes=chunk_bytes)
    chunks = []
    for entry in parts['parts']:
        chunk = (Path(str(path) + '.parts') / entry['name']).read_bytes()
        if len(chunk) != entry['bytes'] or hashlib.sha256(chunk).hexdigest() != entry['sha256']:
            raise ValueError('HTTP archive part differs before original removal')
        chunks.append(chunk)
    packed = b''.join(chunks)
    if len(packed) != parts['original_bytes'] or hashlib.sha256(packed).hexdigest() != parts['original_sha256']:
        raise ValueError('HTTP archive decoded bytes differ before original removal')
    with tarfile.open(fileobj=io.BytesIO(packed), mode='r:gz') as archive:
        members = archive.getmembers()
        if len(members) != len(bodies) or {m.name for m in members} != set(bodies):
            raise ValueError('HTTP archive member scope differs')
        for member in members:
            if not member.isfile() or archive.extractfile(member).read() != bodies[member.name]:
                raise ValueError('HTTP archived body differs before original removal')
    path.unlink()
    if read_artifact(path) != packed:
        raise ValueError('HTTP archive reader differs; response originals retained')
    for name in bodies:
        (folder / name).unlink()
    result = {'archived_at': datetime.now(timezone.utc).isoformat(), 'receipt_index_sha256': hashlib.sha256(index_raw).hexdigest(),
              'archived_body_n': len(bodies), 'receipts_n': len(receipts),
              'failed_http_n': sum(r.get('http_status') != 200 or bool(r.get('error')) for r in receipts),
              'archive_sha256': parts['original_sha256'], 'archive_bytes': parts['original_bytes'],
              'capture_clock_independently_authenticated': False,
              'execution_code_sha256': code_sha256}
    with (folder / 'RAW_ARCHIVE_AUDIT.json').open('x') as output:
        json.dump(result, output, indent=2)
    return result
