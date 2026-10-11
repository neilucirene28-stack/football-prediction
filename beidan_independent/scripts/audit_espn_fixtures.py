"""Audit five proposed fixture bindings against archived ESPN scoreboards."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from beidan_bd1.espn_fixture import audit_fixture
from beidan_bd1.snapshot import _datetime


def main():
    root = Path(__file__).resolve().parents[1]
    verified_at = datetime.now(timezone.utc).isoformat()
    folder = root / "data_sample/espn_scoreboard_bb0c436"
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    captures = {}
    for m in manifest:
        raw = (folder / Path(m["file"]).name).read_bytes()
        if m["http_status"] != 200 or not hashlib.sha256(raw).hexdigest().startswith(m["sha256_16"]):
            raise ValueError("原始响应摘要/状态与收据矛盾")
        if not _datetime(m["start_utc"], "start") <= _datetime(m["end_utc"], "end") <= _datetime(verified_at, "verified_at"):
            raise ValueError("收据起止时间矛盾")
        captures[m["league"], m["date_param"]] = raw
    old = json.loads((root / "outputs/20261010_179_shadow.json").read_text())
    pool = {r["seq"]: r for r in old["predictions"]}
    candidates = [json.loads(line) for line in (folder / "fixture_matching.jsonl").read_text().splitlines() if line]
    proposals = [json.loads(line) for line in (root / "data_sample/espn_eu_f53bc2f/matching.jsonl").read_text().splitlines() if line]
    records = []
    for m in candidates:
        seq = int(m["seq"])
        roster = pool[seq]
        if (m["period"] != roster["period"] or m["beidan_home"] != roster["home"]
                or m["beidan_away"] != roster["away"]):
            raise ValueError("提出的北单匹配与原始输入池不同")
        pair = {p["team_cn"]: str(p["espn_id"]) for p in proposals if p["beidan_seq"] == seq}
        date = _datetime(roster["kickoff_at"], "kickoff").astimezone(timezone.utc).strftime("%Y%m%d")
        row = audit_fixture(captures[m["league"], date], event_id=m["espn_event_id"],
            league_slug=m["league"], expected_home_id=pair[roster["home"]],
            expected_away_id=pair[roster["away"]], roster=roster, verified_at=verified_at)
        records.append(row)
    if len({r["seq"] for r in records}) != 5 or {r["seq"] for r in records} != {18,19,22,23,24}:
        raise ValueError("提出匹配集合不完整或重复")
    output = root / "outputs/espn_fixture_audit"
    output.mkdir(exist_ok=True)
    path = output / ("audit_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    report = {"verified_at": verified_at, "source_commit": "bb0c436",
        "raw_captures_n": len(captures), "structurally_consistent_fixture_n": len(records),
        "canonical_identity_approved_n": 0, "production_eligible": False, "records": records}
    with path.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, allow_nan=False)
    print(json.dumps({k:v for k,v in report.items() if k != "records"}, ensure_ascii=False))
    print(path)


if __name__ == "__main__":
    main()
