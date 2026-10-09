"""Audit explicit halftime linescores against an already-cleaned schedule.

Provider identities stay unapproved; missing halves are never imputed.
"""
from __future__ import annotations

import hashlib
import json
from .snapshot import _datetime


def _goals(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("比分必须为整数或数字字符串")
    if isinstance(value, str) and not value.isascii():
        raise ValueError("比分格式不明确")
    if isinstance(value, str) and not value.isdigit():
        raise ValueError("比分格式不明确")
    result = int(value)
    if not 0 <= result <= 80:
        raise ValueError("比分越界")
    return result


def audit_summary(payload: dict, schedule: dict, *, verified_at: str,
                  raw_bytes: bytes, league_slug: str = "jpn.1") -> tuple[dict, dict]:
    """Require matching provider ID, stage, time, sides, and full-time score."""
    if json.loads(raw_bytes) != payload:
        raise ValueError("用于摘要的原始字节与解析对象不一致")
    now = _datetime(verified_at, "verified_at")
    header = payload.get("header", {})
    if (header.get("id") != schedule["provider_match_id"]
            or header.get("league", {}).get("slug") != league_slug
            or header.get("league", {}).get("id") != schedule["provider_league_id"]
            or header.get("season", {}).get("year") != schedule["season_year"]
            or header.get("season", {}).get("type") != schedule["season_type"]):
        raise ValueError("summary比赛ID或赛季阶段不一致")
    competitions = header.get("competitions")
    if not isinstance(competitions, list) or len(competitions) != 1:
        raise ValueError("summary比赛结构不明确")
    c = competitions[0]
    status = c.get("status", {}).get("type", {})
    kickoff = _datetime(c.get("date"), "summary.date")
    if (c.get("id") != header["id"] or kickoff != _datetime(schedule["kickoff_at"], "schedule.date")
            or kickoff >= now or status.get("completed") is not True
            or status.get("state") != "post" or status.get("name") != "STATUS_FULL_TIME"):
        raise ValueError("summary时间、90分钟结束状态或ID矛盾")
    competitors = c.get("competitors", [])
    if len(competitors) != 2 or {x.get("homeAway") for x in competitors} != {"home", "away"}:
        raise ValueError("summary主客不明确")
    sides = {x["homeAway"]: x for x in competitors}
    halves, warnings = {}, []
    for side, x in sides.items():
        if (x.get("id") != schedule[f"provider_{side}_id"]
                or x.get("team", {}).get("id") != x.get("id")
                or _goals(x.get("score")) != schedule[f"ft_{side}"]):
            raise ValueError("summary主客身份或全场比分与schedule矛盾")
        ls = x.get("linescores")
        if ls is None or ls == []:
            halves[side] = None
            warnings.append(f"{side}_halftime_missing")
        else:
            if not isinstance(ls, list) or len(ls) != 2:
                raise ValueError("90分钟linescores必须明确为两个半场")
            parts = [_goals(item.get("displayValue")) for item in ls]
            if sum(parts) != schedule[f"ft_{side}"]:
                raise ValueError("半场之和与全场比分矛盾")
            halves[side] = parts[0]
    goals = [e for e in payload.get("keyEvents", []) if e.get("scoringPlay") is True]
    goal_ids = [e.get("id") for e in goals]
    if len(set(goal_ids)) != len(goals) or any(not x for x in goal_ids):
        raise ValueError("进球事件ID缺失或重复")
    full_counts = {side: 0 for side in sides}
    first_counts = {side: 0 for side in sides}
    usable = True
    for event in goals:
        team = event.get("team", {}).get("id")
        period = event.get("period", {}).get("number")
        side = next((s for s in sides if sides[s]["id"] == team), None)
        if side is None or period not in (1, 2) or event.get("ownGoal") is True:
            usable = False
            continue
        full_counts[side] += 1
        first_counts[side] += period == 1
    event_complete = usable and all(full_counts[s] == schedule[f"ft_{s}"] for s in sides)
    if event_complete:
        for side in sides:
            if halves[side] is not None and first_counts[side] != halves[side]:
                raise ValueError("显式半场与完整进球事件矛盾")
    else:
        warnings.append("goal_events_incomplete_or_ambiguous_not_used_to_infer_halftime")
    record = {**schedule, "ht_home": halves["home"], "ht_away": halves["away"],
              "verified_at": verified_at, "result_available_at": None,
              "identity_verified": False, "full_chain_eligible": False,
              "halftime_source": "header.competitions[0].competitors[*].linescores",
              "summary_sha256": hashlib.sha256(raw_bytes).hexdigest()}
    return record, {"explicit_halftime_available": all(v is not None for v in halves.values()),
                    "complete_goal_events_crosscheck": event_complete,
                    "goal_events_n": len(goals), "warnings": warnings,
                    "production_eligible": False}
