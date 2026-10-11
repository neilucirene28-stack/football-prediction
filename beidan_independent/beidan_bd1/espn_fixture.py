"""Audit proposed provider fixture bindings without approving canonical IDs."""
import hashlib
import json

from .snapshot import _datetime


def audit_fixture(raw: bytes, *, event_id: str, league_slug: str,
                  expected_home_id: str, expected_away_id: str,
                  roster: dict, verified_at: str) -> dict:
    payload = json.loads(raw)
    now = _datetime(verified_at, "verified_at")
    leagues = [l for l in payload.get("leagues", []) if l.get("slug") == league_slug
               and str(l.get("uid", "")).startswith("s:600~l:")]
    events = [e for e in payload.get("events", []) if e.get("id") == event_id]
    if len(leagues) != 1 or len(events) != 1 or roster.get("sport") != "football":
        raise ValueError("来源足球联赛、唯一比赛ID或北单运动类型不明确")
    event = events[0]
    league_id = leagues[0].get("id")
    season = event.get("season", {})
    if (not isinstance(league_id, str) or not league_id.isdigit()
            or leagues[0]["uid"] != "s:600~l:" + league_id
            or any(isinstance(season.get(k), bool) or not isinstance(season.get(k), int)
                   or season[k] < 1 for k in ("year", "type"))):
        raise ValueError("来源联赛ID或赛季阶段不明确")
    if event.get("uid") != leagues[0]["uid"] + "~e:" + event_id:
        raise ValueError("来源赛事与足球联赛事件空间矛盾")
    comps = event.get("competitions", [])
    if len(comps) != 1:
        raise ValueError("比赛结构不唯一")
    c = comps[0]
    kickoff = _datetime(event.get("date"), "event.date")
    status = c.get("status", {}).get("type", {})
    if (c.get("id") != event_id or c.get("timeValid") is not True
            or _datetime(c.get("date"), "competition.date") != kickoff
            or _datetime(roster.get("kickoff_at"), "roster.kickoff_at") != kickoff
            or kickoff <= now or status.get("state") != "pre"
            or status.get("name") != "STATUS_SCHEDULED" or status.get("completed") is not False):
        raise ValueError("赛程ID、时间、赛前状态或开球时刻不匹配")
    players = c.get("competitors", [])
    if len(players) != 2 or {p.get("homeAway") for p in players} != {"home", "away"}:
        raise ValueError("来源主客方向不明确")
    sides = {p["homeAway"]: p for p in players}
    for side, expected in (("home", expected_home_id), ("away", expected_away_id)):
        p = sides[side]
        if (not isinstance(expected, str) or not expected.isdigit()
                or p.get("id") != expected or p.get("team", {}).get("id") != expected
                or p.get("type") != "team"):
            raise ValueError("来源球队ID或主客不匹配")
    if expected_home_id == expected_away_id:
        raise ValueError("主客来源ID相同")
    return {"period": roster["period"], "seq": roster["seq"], "match_id": roster["match_id"],
        "proposed_provider_event_id": event_id, "provider_home_id": expected_home_id,
        "provider_away_id": expected_away_id, "provider_league_slug": league_slug,
        "provider_league_id": league_id, "season_year": season["year"],
        "season_type": season["type"],
        "provider_home": sides["home"]["team"]["displayName"],
        "provider_away": sides["away"]["team"]["displayName"],
        "kickoff_at": kickoff.isoformat(), "verified_at": verified_at,
        "raw_sha256": hashlib.sha256(raw).hexdigest(), "provider_structure_consistent": True,
        "canonical_identity_approved": False, "production_eligible": False,
        "official_handicap": roster.get("handicap"),
        "official_handicap_new_source_verified": False,
        "external_bookmaker_handicap_imported": False,
        "scheduled_placeholder_score_imported": False}
