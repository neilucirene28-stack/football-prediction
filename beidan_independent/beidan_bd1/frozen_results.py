"""Bind newly received ESPN full-time summaries to an existing sealed pool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .espn_summary import _goals, audit_summary
from .frozen_settlement import load_bundle, _utc_now


def import_espn_result(raw_bytes, *, pool, verified_at, expected_league_slug=None):
    """Use explicit 90-minute FT/HT; never infer missing halftime scores."""
    payload = json.loads(raw_bytes)
    header = payload.get("header", {})
    eid = header.get("id")
    matches = [f for f in pool.values() if f.get("provider_match_id") == eid]
    if len(matches) != 1:
        raise ValueError("赛果来源比赛必须唯一绑定冻结池")
    fixture = matches[0]
    if header.get("league", {}).get("id") != fixture["provider_league_id"]:
        raise ValueError("赛果联赛ID与冻结池不一致")
    competitions = header.get("competitions")
    if not isinstance(competitions, list) or len(competitions) != 1:
        raise ValueError("赛果比赛结构不明确")
    sides = {x.get("homeAway"): x for x in competitions[0].get("competitors", [])}
    if set(sides) != {"home", "away"}:
        raise ValueError("赛果主客不明确")
    slug = header.get("league", {}).get("slug")
    if not isinstance(slug, str) or not slug:
        raise ValueError("赛果联赛slug缺失")
    if expected_league_slug is not None and slug != expected_league_slug:
        raise ValueError("赛果联赛slug与原封存审核不一致")
    schedule = {**fixture, "ft_home": _goals(sides["home"].get("score")),
                "ft_away": _goals(sides["away"].get("score")), "regular_time": True}
    record, audit = audit_summary(payload, schedule, verified_at=verified_at, raw_bytes=raw_bytes, league_slug=slug)
    # One missing side prevents half/full scoring; preserve explicit fields in
    # the audit without treating the other half as a zero.
    if record["ht_home"] is None or record["ht_away"] is None:
        record["explicit_halftime_fields"] = {"home": record["ht_home"], "away": record["ht_away"]}
        record["ht_home"] = record["ht_away"] = None
    record["result_source"] = {"name": "ESPN_summary_structure_audit", "status": "ok",
                               "available_at": verified_at, "raw_sha256": record["summary_sha256"]}
    record["result_audit"] = audit
    return record


def main():
    parser = argparse.ArgumentParser(description="从新取得的显式90分钟ESPN原件绑定冻结赛果")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--payload", action="append", required=True, help="原始summary JSON，可重复指定")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    payloads = [Path(p).read_bytes() for p in args.payload]
    now = _utc_now().isoformat()
    report, pool, predictions = load_bundle(args.bundle, manifest_sha256=args.manifest_sha256, evaluated_at=now)
    if report["identity_mode"] != "provider_native_espn":
        raise ValueError("ESPN导入器只接受来源ID研究冻结")
    results = [import_espn_result(raw, pool=pool, verified_at=now) for raw in payloads]
    if len({r["match_id"] for r in results}) != len(results):
        raise ValueError("同一冻结比赛重复导入")
    with Path(args.out).open("x", encoding="utf-8") as stream:
        for result in results:
            stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
    print(json.dumps({"verified_at": now, "results_n": len(results),
                      "predicted_result_n": sum(r["match_id"] in predictions for r in results),
                      "production_gate_passed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
