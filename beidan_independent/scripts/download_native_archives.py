"""Download pinned repository source archives, verifying Git blob hashes.

This does not change source receipt clocks or confer canonical identity approval.
The plan must contain paths actually listed in the pinned repository tree.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    args = ap.parse_args()
    plan = json.loads(Path(args.plan).read_text())
    root = Path(__file__).resolve().parents[2]
    log = []

    def fetch(entry):
        path = root / entry["path"]
        url = (f"https://raw.githubusercontent.com/{plan['repository']}/"
               f"{plan['source_commit']}/{entry['path']}")
        start = datetime.now(timezone.utc).isoformat()
        if path.exists():
            raw = path.read_bytes()
            cached = True
        else:
            with urlopen(url, timeout=40) as response:
                if response.status != 200:
                    raise ValueError("archive download HTTP status")
                raw = response.read()
            cached = False
        git_sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        if len(raw) != entry["size"] or git_sha != entry["sha"]:
            raise ValueError(f"pinned Git blob differs: {entry['path']}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cached:
            path.write_bytes(raw)
        return {"path": entry["path"], "git_blob_sha": git_sha,
                "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                "repository_download_started_at": start,
                "repository_download_verified_at": datetime.now(timezone.utc).isoformat(),
                "cached": cached}

    errors = []
    with ThreadPoolExecutor(max_workers=6) as executor:
        jobs = {executor.submit(fetch, e): e for e in plan["entries"]}
        for n, future in enumerate(as_completed(jobs), 1):
            try:
                log.append(future.result())
            except Exception as exc:
                errors.append({"path": jobs[future]["path"], "reason": str(exc)})
            if n % 30 == 0 or n == len(jobs):
                print(json.dumps({"finished": n, "total": len(jobs), "errors": len(errors)}), flush=True)
    output = root / "beidan_independent/data_sample/native_archive_download_receipts.json"
    output.write_text(json.dumps({"source_commit": plan["source_commit"],
                      "repository": plan["repository"], "files": sorted(log, key=lambda r: r["path"]),
                      "errors": errors}, indent=2))
    if errors:
        raise SystemExit(json.dumps(errors))


if __name__ == "__main__":
    main()
