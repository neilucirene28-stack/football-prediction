"""Reproduce the 30 raw-summary halftime audit without approving identities."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from beidan_bd1.espn_schedule import parse_schedule, deduplicate
from beidan_bd1.espn_summary import audit_summary
from beidan_bd1.snapshot import _datetime


def main():
    root = Path(__file__).resolve().parents[1]
    verified_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for team in ("7115", "7102", "7476", "7477"):
        payload = json.loads((root / f"data_sample/espn_j1_9b4f6b8/espn_team_{team}.json").read_bytes())
        records, _ = parse_schedule(payload, expected_team_id=team, verified_at=verified_at)
        rows.extend(records)
    schedule = {r["provider_match_id"]: r for r in deduplicate(rows)}
    folder = root / "data_sample/espn_j1_eeb44fc"
    manifest = (folder / "MANIFEST.md").read_text()
    entries = re.findall(r"^\| (401\d+) \| ([^|]+) \| ([^|]+) \| 200 \| ([0-9a-f]+) \| (\d+) \|", manifest, re.M)
    if len(entries) != 30 or len({e[0] for e in entries}) != 30 or {e[0] for e in entries} != set(schedule):
        raise ValueError("收据集合与30个唯一常规FT比赛不一致")
    results, rejects = [], []
    for eid, start, end, prefix, size in entries:
        raw = (folder / f"espn_summary_{eid}.json").read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if len(raw) != int(size) or not sha.startswith(prefix.strip()):
            raise ValueError("收据大小/摘要不一致：" + eid)
        if not _datetime(start.strip(), "receipt.start") <= _datetime(end.strip(), "receipt.end") <= _datetime(verified_at, "verified_at"):
            raise ValueError("收据起止时间不合法")
        try:
            row, report = audit_summary(json.loads(raw), schedule[eid], verified_at=verified_at, raw_bytes=raw)
            row["source_receipt_claim"] = {"start_utc": start.strip(), "end_utc": end.strip(),
                "http_status": 200, "independently_verified_capture_clock": False}
            results.append({"record": row, "audit": report})
        except ValueError as error:
            rejects.append({"provider_match_id": eid, "reason": str(error), "sha256": sha})
    summary = {"verified_at": verified_at, "source_commit": "eeb44fc", "expected_unique_n": 30,
        "audited_n": len(results), "rejected_n": len(rejects),
        "explicit_ht_n": sum(r["audit"]["explicit_halftime_available"] for r in results),
        "complete_goal_event_crosscheck_n": sum(r["audit"]["complete_goal_events_crosscheck"] for r in results),
        "identity_approved_n": 0, "model_imported_n": 0, "production_eligible": False,
        "results": results, "rejected": rejects}
    output = root / "outputs/espn_summary_audit"
    output.mkdir(exist_ok=True)
    path = output / ("audit_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, allow_nan=False)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False))
    print(path)


if __name__ == "__main__":
    main()
