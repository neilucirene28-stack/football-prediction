# xiaodianhuo.py — 小店欢批量 JSON 适配器（v2.0 XDH_JCZQ_BATCH，时区修正版）
import logging
from datetime import datetime
from typing import Any

from ..canonical import (
    CanonicalMatch,
    MatchInfo,
    ModuleState,
    SportteryOdds,
    beijing_str_to_utc,
)

logger = logging.getLogger("football-prediction-api")


# 竞彩官方赔率键位语义：3=主胜、1=平、0=客胜（竞彩惯例）
_SPORTTERY_ODDS_KEY_MAP = {
    "3": "home_odds",
    "1": "draw_odds",
    "0": "away_odds",
}


def _parse_iso_utc(ts: str) -> datetime | None:
    """解析 ISO 格式时间戳（UTC Z 或带偏移）为 timezone-aware UTC。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=None).replace(tzinfo=None)
            # ISO 时间戳按 UTC 处理
            from datetime import timezone
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except ValueError:
        return None


def _float_or_none(v: Any):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_match_time(v: str) -> datetime | None:
    """比赛开赛时间：北京时间字符串 → timezone-aware UTC。"""
    if not v:
        return None
    try:
        return beijing_str_to_utc(v, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _build_sporttery(sporttery_node: dict, captured_at: datetime | None) -> list:
    result = []
    for zh_market, market_key in (("WDL", "wdl"), ("NWDL", "nwdl")):
        market = sporttery_node.get(zh_market)
        if not isinstance(market, dict):
            continue
        odds = market.get("odds", {})
        if not isinstance(odds, dict):
            continue
        result.append(SportteryOdds(
            market=market_key,
            home_odds=_float_or_none(odds.get("3")),   # 3=主胜
            draw_odds=_float_or_none(odds.get("1")),   # 1=平
            away_odds=_float_or_none(odds.get("0")),   # 0=客胜
            observed_at=captured_at,
            raw={zh_market: market},
        ))
    return result


def _parse_details_states(details_node: dict) -> list:
    states = []
    for name, mod in details_node.items():
        status = mod.get("status", "missing")
        if status == "success":
            quality = "complete"
        elif status == "failed":
            reason = mod.get("reason", "")
            quality = "fetch_failed"
            states.append(ModuleState(
                name=f"detail.{name}",
                quality=quality,
                fetch_status=status,
                fetch_reason=reason,
            ))
            continue
        else:
            quality = "not_collected"
        states.append(ModuleState(
            name=f"detail.{name}",
            quality=quality,
            fetch_status=status,
            fetch_reason=mod.get("reason"),
        ))
    return states


def adapt_xiaodianhuo_match(match_entry: dict, exported_at: str | None = None) -> CanonicalMatch:
    """将小店欢批量 JSON 中的单场比赛解析为 CanonicalMatch。

    已知限制（如实反映）:
    - details 接口失败时仅标记 fetch_failed
    - FIFA 排名保留在 fifa_rank（不入积分排名表）
    """
    match_node = match_entry.get("match", {})
    details_node = match_entry.get("details", {})
    ext_id = str(match_node.get("match_id", "")).strip()

    captured_at = None
    ts = match_entry.get("capturedAt") or exported_at
    if ts:
        captured_at = _parse_iso_utc(ts)

    kickoff = _parse_match_time(match_node.get("match_time"))

    match_info = MatchInfo(
        source="xiaodianhuo",
        external_match_id=ext_id,
        external_match_id2=match_node.get("match_id2") or None,
        competition=match_node.get("league") or None,
        home_team=match_node.get("home") or None,
        away_team=match_node.get("away") or None,
        kickoff_at=kickoff,
        kickoff_known=kickoff is not None,
        match_status=None,
        sporttery_no=match_node.get("number_key") or None,
    )

    cm = CanonicalMatch(
        source="xiaodianhuo",
        external_match_id=ext_id,
        match=match_info,
        raw_payload=match_entry,
        collected_at=captured_at,
    )

    cm.module_states.extend(_parse_details_states(details_node))

    sporttery = match_node.get("sporttery")
    if isinstance(sporttery, dict):
        cm.sporttery_odds = _build_sporttery(sporttery, captured_at)

    rank = match_node.get("rank")
    if isinstance(rank, dict) and rank:
        cm.fifa_rank = rank

    return cm


def adapt_xiaodianhuo_batch(raw: dict) -> list:
    """批量入口：拆分 matches 数组，返回 CanonicalMatch 列表。"""
    matches = raw.get("matches", [])
    if not isinstance(matches, list):
        raise ValueError("xiaodianhuo: matches must be a list")

    exported_at = raw.get("exportedAt")
    results = []
    for entry in matches:
        if not isinstance(entry, dict):
            logger.warning("xiaodianhuo: skip non-dict match entry")
            continue
        results.append(adapt_xiaodianhuo_match(entry, exported_at))

    if raw.get("total") is not None and len(results) != raw.get("total"):
        logger.warning(
            "xiaodianhuo: total=%s but parsed %s matches",
            raw.get("total"), len(results),
        )
    return results
