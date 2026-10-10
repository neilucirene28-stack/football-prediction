"""Retain received HTTP bodies before status or payload interpretation."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import urllib.error
import urllib.request


def clock():
    return datetime.now(timezone.utc).isoformat()


def capture_response(url, folder, filename, *, opener=urllib.request.urlopen):
    receipt = {'url': url, 'start_utc': clock()}
    raw = None
    try:
        try:
            response = opener(url, timeout=45)
        except urllib.error.HTTPError as error:
            # HTTPError is also a received response. Keep its body, including
            # a network denial; this does not authorize retrying that request.
            response = error
        with response:
            receipt['http_status'] = response.status
            raw = response.read()
        receipt['end_utc'] = clock()
    except Exception as error:
        receipt.update(end_utc=clock(), error=str(error))
        return receipt, None
    # Exclusive write prevents accidental replacement of earlier evidence.
    # Disk failures propagate instead of becoming a misleading HTTP failure.
    with (Path(folder) / filename).open('xb') as output:
        output.write(raw)
    receipt.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                   local_file=filename)
    return receipt, raw
