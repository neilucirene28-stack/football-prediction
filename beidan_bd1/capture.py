"""Append-only receipt for newly collected BD-1 pre-match source payloads.

The ingest time is the earliest claim this adapter makes about availability.
It never backdates old file mtimes or asserts provider-side publication time.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from .snapshot import _datetime, _MATCH_ID


_KINDS = frozenset(("fixture", "identity", "market", "form", "lineup", "result"))


def capture_payload(*, payload_file: str | Path, root: str | Path,
                    period: str, match_id: str, source: str, kind: str) -> dict:
    """Read a local provider response, stamp now and archive it once.

    A source collector should call this immediately after retrieval. A later
    import of an older response receives a *later* availability time.
    """
    if (kind not in _KINDS or any(not isinstance(x, str) or not _MATCH_ID.fullmatch(x)
                                  for x in (period, match_id, source))):
        raise ValueError("采集来源、比赛ID或事件类型非法")
    raw = Path(payload_file).read_bytes()
    if not raw or len(raw) > 2_000_000:
        raise ValueError("采集payload为空或超过单场上限")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("采集payload须为UTF-8 JSON") from exc
    if not isinstance(value, (dict, list)):
        raise ValueError("采集payload须为JSON对象或数组")
    digest = hashlib.sha256(raw).hexdigest()
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{match_id}__{source}__{kind}__{stamp}__{digest[:12]}"
    folder = Path(root) / period
    folder.mkdir(parents=True, exist_ok=True)
    payload_path = folder / f"{name}.json"
    receipt_path = folder / f"{name}.receipt.json"
    receipt = {"period": period, "match_id": match_id, "source": source,
               "kind": kind, "available_at": now.isoformat(),
               "sha256": digest, "bytes": len(raw),
               "payload_path": str(payload_path.relative_to(root)),
               "model_eligibility": "unassessed",
               "available_at_basis": "local_ingest_clock_after_payload_read"}
    fd = os.open(payload_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        data = (json.dumps(receipt, sort_keys=True, ensure_ascii=False) + "\n").encode()
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        # An orphan payload is evidence of an incomplete attempt; never alter
        # an already written source file after a receipt failure.
        raise
    return receipt


def verify_receipt(receipt: dict, root: str | Path) -> bool:
    if (not isinstance(receipt, dict) or not isinstance(receipt.get("sha256"), str)
            or not isinstance(receipt.get("payload_path"), str)):
        raise ValueError("采集凭证字段缺失")
    folder = Path(root).resolve()
    file = (folder / receipt["payload_path"]).resolve()
    if not file.is_relative_to(folder):
        raise ValueError("采集凭证路径越界")
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt["sha256"] or len(raw) != receipt.get("bytes"):
        raise ValueError("采集归档内容与摘要不一致")
    return True


def receipt_source(receipt: dict, root: str | Path) -> dict:
    """Verify payload bytes before binding its first local ingest time."""
    verify_receipt(receipt, root)
    if (not isinstance(receipt.get("source"), str)
            or not _MATCH_ID.fullmatch(receipt["source"])):
        raise ValueError("采集凭证来源非法")
    _datetime(receipt.get("available_at"), "available_at")
    return {"name": receipt["source"], "source_match_id": None,
            "available_at": receipt["available_at"], "status": "ok"}


def main() -> None:
    ap = argparse.ArgumentParser(description="接收并不可覆盖地归档北单单场原始输入")
    ap.add_argument("--payload", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--period", required=True)
    ap.add_argument("--match-id", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--kind", choices=sorted(_KINDS), required=True)
    args = ap.parse_args()
    print(json.dumps(capture_payload(payload_file=args.payload, root=args.out,
                                     period=args.period, match_id=args.match_id,
                                     source=args.source, kind=args.kind), ensure_ascii=False))


if __name__ == "__main__":
    main()
