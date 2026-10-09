"""Audit genuine API-Football fixture body; never infer joins from clock alone."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from beidan_bd1.snapshot import _datetime
from beidan_bd1.af_history import _integer


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "data_sample/af_true"
    raw = (folder / "af_fixtures_raw_20261010.json").read_bytes()
    receipt = json.loads((folder / "RECEIPT.json").read_text())
    now = datetime.now(timezone.utc).isoformat()
    sha = hashlib.sha256(raw).hexdigest()
    if (receipt["http_status"] != 200 or receipt["size_bytes"] != len(raw) or receipt["sha256"] != sha
            or not _datetime(receipt["start_utc"], "start") <= _datetime(receipt["end_utc"], "end") <= _datetime(now, "now")):
        raise ValueError("真正响应与请求收据大小/摘要/状态/时间不一致")
    payload = json.loads(raw)
    if (not isinstance(payload, dict) or payload.get("get") != "fixtures" or payload.get("errors") != []
            or payload.get("parameters") != {"date":"2026-10-10", "timezone":"Asia/Shanghai"}
            or payload.get("parameters") != receipt["params"]
            or payload.get("paging") != {"current":1, "total":1}
            or not isinstance(payload.get("response"), list)
            or payload.get("results") != len(payload["response"])):
        raise ValueError("API顶层结构、错误、参数或分页不完整")
    seen, leagues, records = set(), {}, []
    for event in payload["response"]:
        f, l, t = event["fixture"], event["league"], event["teams"]
        for value, label in ((f.get('id'), 'fixture.id'), (t['home'].get('id'), 'home.id'),
                             (t['away'].get('id'), 'away.id'), (l.get('id'), 'league.id'),
                             (l.get('season'), 'season')):
            _integer(value, label, 1)
        if (isinstance(f.get("id"), bool) or not isinstance(f.get("id"), int)
                or f["id"] in seen or t["home"]["id"] == t["away"]["id"]):
            raise ValueError("来源比赛ID重复/非法或主客ID相同")
        seen.add(f["id"])
        kickoff = _datetime(f["date"], "fixture.date")
        if (int(kickoff.timestamp()) != f["timestamp"] or f["timezone"] != "Asia/Shanghai"
                or f["date"][:10] != "2026-10-10" or f["status"]["short"] not in {"NS","CANC","PST"}
                or kickoff <= _datetime(now, "now")):
            raise ValueError("来源时区、日期、epoch、赛前状态或开球时间不合法")
        if (event["goals"] != {"home":None,"away":None}
                or any(s != {"home":None,"away":None} for s in event["score"].values())):
            raise ValueError("未来未开球比赛有未知比分；不能当赛果")
        key = (l["id"], l["season"])
        identity = {k:l[k] for k in ("id","name","country","season")}
        if key in leagues and leagues[key] != identity:
            raise ValueError("同来源联赛ID/赛季名称矛盾")
        leagues[key] = identity
        records.append({"provider_match_id": f["id"], "provider_home_id": t["home"]["id"],
            "provider_away_id": t["away"]["id"], "home":t["home"]["name"], "away":t["away"]["name"],
            "kickoff_at": kickoff.isoformat(), "status": f["status"]["short"], "league": identity,
            "scheduled_status_eligible": f["status"]["short"] == "NS",
            "verified_at": now, "provider_capture_clock_independently_verified":False,
            "canonical_identity_approved":False, "beidan_fixture_binding":None,
            "production_eligible":False})
    summary = {"verified_at":now, "source_commit":"8ee2007", "raw_sha256":sha,
        "raw_bytes":len(raw), "response_n":len(records),
        "statuses":dict(Counter(r["status"] for r in records)), "unique_league_season_n":len(leagues),
        "league_catalog":list(leagues.values()), "canonical_identity_approved_n":0,
        "beidan_bound_n":0, "model_imported_n":0, "production_eligible":False, "records":records}
    output = root / "outputs/af_response_audit"
    output.mkdir(exist_ok=True)
    path = output / ("audit_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, allow_nan=False)
    print(json.dumps({k:v for k,v in summary.items() if k not in ("records","league_catalog")},ensure_ascii=False))
    print(path)


if __name__ == "__main__":
    main()
