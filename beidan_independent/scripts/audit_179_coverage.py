"""Audit full roster and archive integrity; timestamps alone never bind teams."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from beidan_bd1.snapshot import _datetime


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "data_sample/coverage_7c1799b"
    now = datetime.now(timezone.utc).isoformat()
    ledger = [json.loads(line) for line in (folder / "beidan_179_coverage_20261010.jsonl").read_text().splitlines() if line]
    roster = json.loads((root / "outputs/20261010_179_shadow.json").read_text())["predictions"]
    by_seq = {int(r["seq"]): r for r in ledger}
    if len(ledger) != 179 or len(by_seq) != 179 or set(by_seq) != set(range(11,190)):
        raise ValueError("179场覆盖账本集合不完整或重复")
    for r in roster:
        x = by_seq[r["seq"]]
        if (x["period"] != r["period"] or x["beidan_home"] != r["home"]
                or x["beidan_away"] != r["away"] or x["beidan_league"] != r["league"]
                or _datetime(x["beidan_kickoff_utc"], "ledger.time") != _datetime(r["kickoff_at"], "roster.time")
                or _datetime(x["beidan_kickoff"], "ledger.local_time") != _datetime(r["kickoff_at"], "roster.time")):
            raise ValueError("账本球队、赛事、开球或期号与原池不一致")
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    mapping, events, archives = {}, {}, []
    for m in manifest:
        if m.get("espn_code"):
            mapping.setdefault(m["league_cn"], set()).add(m["espn_code"])
        if m.get("http_status") != 200:
            continue
        path = folder / Path(m["file"]).name
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if len(raw) != m["size"] or not sha.startswith(m["sha256_16"]):
            raise ValueError("原件大小或摘要不一致")
        if not _datetime(m["start_utc"], "start") <= _datetime(m["end_utc"], "end") <= _datetime(now, "verified_at"):
            raise ValueError("请求起止时间非法")
        payload = json.loads(raw)
        if len(payload.get("events", [])) != m["events"]:
            raise ValueError("事件数量与收据不一致")
        leagues = [l for l in payload.get("leagues", []) if l.get("slug") == m["espn_code"]
                   and str(l.get("uid", "")).startswith("s:600~l:")]
        if payload.get("events") and len(leagues) != 1:
            raise ValueError("存在事件但来源足球联赛结构不明确")
        archives.append({"file": path.name, "sha256": sha, "events": m["events"]})
        for e in payload.get("events", []):
            comps = e.get("competitions", [])
            if (len(comps) != 1 or comps[0].get("id") != e.get("id")
                    or e.get("uid") != leagues[0]["uid"] + "~e:" + e["id"]):
                raise ValueError("来源事件ID或足球事件空间矛盾")
            c = comps[0]
            time = _datetime(e["date"], "event.date")
            if _datetime(c["date"], "competition.date") != time:
                raise ValueError("来源两级开球矛盾")
            sides = {p["homeAway"]: p for p in c["competitors"]}
            if len(c["competitors"]) != 2 or set(sides) != {"home", "away"}:
                raise ValueError("来源主客方向不明确")
            for p in sides.values():
                if p.get("type") != "team" or p.get("id") != p.get("team", {}).get("id"):
                    raise ValueError("来源队ID不一致")
            proposal = {"provider_event_id": e["id"], "provider_league_slug": m["espn_code"],
                "kickoff_at": time.isoformat(), "season": e.get("season"),
                "provider_home_id": sides["home"]["id"], "provider_away_id": sides["away"]["id"],
                "provider_home": sides["home"]["team"]["displayName"],
                "provider_away": sides["away"]["team"]["displayName"],
                "status": c.get("status", {}).get("type", {}), "time_valid": c.get("timeValid"),
                "raw_file": path.name}
            key = m["espn_code"], e["id"]
            if key in events and any(events[key][k] != proposal[k] for k in ("kickoff_at", "provider_home_id", "provider_away_id", "season")):
                raise ValueError("同来源事件跨归档观察矛盾")
            events[key] = proposal
    records = []
    for r in roster:
        codes = mapping.get(r["league"], set())
        kickoff = _datetime(r["kickoff_at"], "roster.time")
        candidates = [e for e in events.values() if e["provider_league_slug"] in codes
                      and _datetime(e["kickoff_at"], "provider.time") == kickoff
                      and e["time_valid"] is True and e["status"].get("state") == "pre"
                      and e["status"].get("name") == "STATUS_SCHEDULED"
                      and e["status"].get("completed") is False and kickoff > _datetime(now, "verified_at")]
        records.append({"period": r["period"], "seq": r["seq"], "home": r["home"], "away": r["away"],
            "league": r["league"], "candidate_n": len(candidates), "candidates": candidates,
            "scope": "same_utc_and_proposed_league_code_only",
            "canonical_identity_approved": False, "league_mapping_approved": False,
            "production_eligible": False})
    summary = {"verified_at": now, "source_commit": "7c1799b", "roster_n": len(records),
        "manifest_entries_n": len(manifest), "successful_raw_archives_n": len(archives),
        "failed_http_requests_n": sum(m.get("http_status") not in (None,200) for m in manifest),
        "manifest_no_mapping_n": sum(m.get("status") == "NO_MAPPING" for m in manifest),
        "unique_source_events_n": len(events),
        "candidate_count_histogram": dict(Counter(r["candidate_n"] for r in records)),
        "any_espn_time_and_proposed_league_candidate_n": sum(r["candidate_n"] > 0 for r in records),
        "name_or_identity_approved_n": 0, "af_archive_not_audited_by_this_script": True,
        "production_eligible": False, "archives": archives, "records": records}
    output = root / "outputs/coverage_179_audit"
    output.mkdir(exist_ok=True)
    path = output / ("audit_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, allow_nan=False)
    print(json.dumps({k:v for k,v in summary.items() if k not in ("records","archives")}, ensure_ascii=False))
    print(path)


if __name__ == "__main__":
    main()
