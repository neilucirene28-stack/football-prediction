"""Read original bytes from direct files or hash-checked lossless parts."""
import gzip
import hashlib
import json
from pathlib import Path


def read_artifact(path):
    path = Path(path)
    if path.exists():
        return path.read_bytes()
    index = json.loads(Path(str(path) + '.parts.json').read_bytes())
    if index.get('schema') != 'bd1-lossless-parts-1' or index.get('encoding') not in ('identity', 'gzip'):
        raise ValueError('unsupported lossless artifact format')
    parts = index.get('parts')
    if not isinstance(parts, list) or not parts:
        raise ValueError('artifact parts are missing')
    names = [entry.get('name') for entry in parts]
    if (any(not isinstance(name, str) or Path(name).name != name for name in names)
            or len(names) != len(set(names))):
        raise ValueError('ambiguous or unsafe artifact part name')
    folder = Path(str(path) + '.parts')
    chunks = []
    for entry in parts:
        raw = (folder / entry['name']).read_bytes()
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError('artifact part differs from receipt')
        chunks.append(raw)
    packed = b''.join(chunks)
    if len(packed) != index['packed_bytes'] or hashlib.sha256(packed).hexdigest() != index['packed_sha256']:
        raise ValueError('packed artifact digest differs')
    raw = gzip.decompress(packed) if index['encoding'] == 'gzip' else packed
    if len(raw) != index['original_bytes'] or hashlib.sha256(raw).hexdigest() != index['original_sha256']:
        raise ValueError('decoded original artifact digest differs')
    return raw


def pack_artifact(path, *, chunk_bytes=48000, compress=False):
    """Create exclusive parts; keep the direct original for caller verification."""
    if type(chunk_bytes) is not int or chunk_bytes < 1:
        raise ValueError('chunk size must be a positive integer')
    path = Path(path)
    raw = path.read_bytes()
    packed = gzip.compress(raw, mtime=0) if compress else raw
    folder = Path(str(path) + '.parts')
    folder.mkdir(exist_ok=False)
    entries = []
    for offset in range(0, max(len(packed), 1), chunk_bytes):
        chunk = packed[offset:offset + chunk_bytes]
        name = f'part_{offset // chunk_bytes:04d}.bin'
        with (folder / name).open('xb') as output:
            output.write(chunk)
        entries.append({'name': name, 'bytes': len(chunk), 'sha256': hashlib.sha256(chunk).hexdigest()})
    index = {'schema': 'bd1-lossless-parts-1', 'encoding': 'gzip' if compress else 'identity',
             'original_bytes': len(raw), 'original_sha256': hashlib.sha256(raw).hexdigest(),
             'packed_bytes': len(packed), 'packed_sha256': hashlib.sha256(packed).hexdigest(), 'parts': entries}
    with Path(str(path) + '.parts.json').open('x') as output:
        json.dump(index, output, indent=2)
    return index
