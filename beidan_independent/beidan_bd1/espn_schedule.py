"""Parse actual soccer schedule bodies; never infer missing halftime scores."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from .snapshot import _datetime


def parse_schedule(payload: dict, *, expected_team_id: str, verified_at: str,
                   season_year: int = 2026, season_type: int = 14287,
                   league_slug: str = "jpn.1") -> tuple[list[dict], dict]:
    """Keep completed 90-minute games in one explicit season/stage only.

    `verified_at` is a conservative time of checking this archive, not the
    historical result publication or the provider's own timestamp.
    IDs remain provider IDs, not approved Chinese-name/canonical-ID mappings.
    """
    now = _datetime(verified_at, "verified_at")
    if (not isinstance(payload, dict) or payload.get("team", {}).get("id") != expected_team_id
            or not isinstance(payload.get("events"), list)):
        raise ValueError("球队ID或原始schedule结构不一致")
    rows, skipped = [], Counter()
    for event in payload["events"]:
        if (event.get("season", {}).get("year") != season_year
                or event.get("seasonType", {}).get("type") != season_type):
            skipped["different_season_or_stage"] += 1
            continue
        competitions = event.get("competitions")
        if not isinstance(competitions, list) or len(competitions) != 1:
            raise ValueError("原始比赛competitions结构不明确")
        c = competitions[0]
        if c.get("id") != event.get("id") or event.get("league", {}).get("slug") != league_slug:
            raise ValueError("比赛ID或足球联赛事件空间不一致")
        status = c.get("status", {})
        kind = status.get("type", {})
        if (kind.get("name") != "STATUS_FULL_TIME" or kind.get("completed") is not True
                or kind.get("state") != "post" or status.get("period") != 2):
            skipped["not_proven_completed_90_minutes"] += 1
            continue
        kickoff = _datetime(event.get("date"), "event.date")
        if kickoff >= now:
            raise ValueError("已结束赛事的开球在核验之后")
        if _datetime(c.get("date"), "competition.date") != kickoff:
            raise ValueError("两级比赛开球时间不一致")
        competitors = c.get("competitors", [])
        if len(competitors) != 2 or {x.get("homeAway") for x in competitors} != {"home", "away"}:
            raise ValueError("原始主客方向不明确")
        sides = {x["homeAway"]: x for x in competitors}
        ids, goals = {}, {}
        for side, player in sides.items():
            identity = player.get("id")
            score = player.get("score", {}).get("value")
            if (not isinstance(identity, str) or not identity.isdigit()
                    or player.get("type") != "team" or player.get("team", {}).get("id") != identity
                    or isinstance(score, bool) or not isinstance(score, (int, float))
                    or not 0 <= score <= 80 or score != int(score)):
                raise ValueError("来源主客ID或比分字段无效")
            ids[side], goals[side] = identity, int(score)
        if expected_team_id not in ids.values() or ids["home"] == ids["away"]:
            raise ValueError("查询球队不在此赛事或主客同ID")
        rows.append({"match_id": "espn:" + event["id"], "provider_match_id": event["id"],
                     "kickoff_at": kickoff.isoformat(), "verified_at": verified_at,
                     "result_available_at": None, "regular_time": True, "sport": "football",
                     "provider_home_id": ids["home"], "provider_away_id": ids["away"],
                     "provider_league_id": event["league"]["id"], "season_year": season_year,
                     "season_type": season_type,
                     "home": sides["home"]["team"]["displayName"],
                     "away": sides["away"]["team"]["displayName"],
                     "ft_home": goals["home"], "ft_away": goals["away"],
                     "ht_home": None, "ht_away": None, "identity_verified": False,
                     "full_chain_eligible": False, "query_team_ids": [expected_team_id]})
    return rows, {"observed_n": len(payload["events"]), "kept_n": len(rows), "skipped": dict(skipped)}


def deduplicate(rows: list[dict]) -> list[dict]:
    by_id = {}
    for row in rows:
        identity = row["match_id"]
        if identity in by_id:
            a, b = by_id[identity], row
            fields = ("kickoff_at", "provider_home_id", "provider_away_id", "provider_league_id",
                      "season_year", "season_type", "home", "away", "ft_home", "ft_away")
            if any(a[k] != b[k] for k in fields):
                raise ValueError("同一来源比赛ID的两个观察矛盾")
            a["query_team_ids"] = sorted(set(a["query_team_ids"] + b["query_team_ids"]))
        else:
            by_id[identity] = {**row, "query_team_ids": list(row["query_team_ids"])}
    return sorted(by_id.values(), key=lambda r: (r["kickoff_at"], r["match_id"]))


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "data_sample/espn_j1_9b4f6b8"
    now = datetime.now(timezone.utc).isoformat()
    rows, reports = [], {}
    for identity in ("7115", "7102", "7476", "7477"):
        p = folder / f"espn_team_{identity}.json"
        records, report = parse_schedule(json.loads(p.read_text()), expected_team_id=identity, verified_at=now)
        rows.extend(records)
        reports[identity] = report
    unique = deduplicate(rows)
    output = root / "outputs/espn_j1_clean"
    output.mkdir(exist_ok=True)
    path = output / ("current_stage_ft_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    data = {"verified_at": now, "stage": "2026_jpn1_regular_type14287", "source_commit": "9b4f6b8",
            "provider_capture_clock_independently_verified": False,
            "per_team": reports, "kept_observations_n": len(rows), "unique_ft_n": len(unique),
            "full_chain_eligible_n": 0, "production_eligible": False, "records": unique}
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, allow_nan=False)
    print(json.dumps({k:v for k,v in data.items() if k != "records"},ensure_ascii=False))
    print(str(path))


if __name__ == "__main__":
    main()
