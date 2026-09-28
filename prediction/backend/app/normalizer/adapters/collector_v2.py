# collector_v2.py — 新版采集器（football-collector-v2）单场 JSON 适配器
# 数据来源：/opt/football-collector-v2 采集器每轮落盘的单场比赛 JSON。
# 结构依据 2026-09-23 真实样本（2683678 等）实测，不凭模块名猜测字段。
# 已知保守决策：
# - unclassifiedOdds 中按公司区分的盘口列表（first/last 赔率、盘口文本、updateTime）
#   的三赔位与亚盘水位语义未经业务确认，本版不写入 odds_europe/odds_asia，
#   完整保留在 match_snapshots.raw_payload 供展示与后续确认。
# - 采集器未提供竞彩官方赔率，sporttery_odds 恒为空，不伪造。
import logging
from datetime import datetime
from typing import Any, Optional

from ..canonical import (
    CanonicalMatch,
    MatchInfo,
    ModuleState,
    beijing_str_to_utc,
    QUALITY_COMPLETE,
    QUALITY_FETCH_FAILED,
    QUALITY_NOT_COLLECTED,
)

logger = logging.getLogger("football-prediction-api")

_MODULES = ("overview", "lineup", "history", "europe", "asia", "ranking", "betfair")


def _parse_iso_utc(ts: Any) -> Optional[datetime]:
    """解析 ISO 时间戳（UTC Z 或带偏移）为 timezone-aware UTC。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _overview_identity(modules: dict) -> dict:
    """从概况模块响应中定位含主客队名的身份响应。"""
    items = modules.get("overview")
    if not isinstance(items, list):
        return {}
    for it in items:
        if not isinstance(it, dict) or not it.get("valid"):
            continue
        data = it.get("data")
        inner = data.get("data") if isinstance(data, dict) else None
        if (
            isinstance(inner, dict)
            and str(inner.get("homeTeamName") or "").strip()
            and str(inner.get("awayTeamName") or "").strip()
        ):
            return inner
    return {}


def adapt_collector_v2(raw: dict) -> CanonicalMatch:
    """新版采集器单场比赛 JSON → CanonicalMatch（single 模式）。"""
    if not isinstance(raw, dict):
        raise ValueError("collector_v2: payload must be a dict")
    ext_id = str(raw.get("matchId") or "").strip()
    if not ext_id:
        raise ValueError("collector_v2: missing matchId")

    modules = raw.get("modules") if isinstance(raw.get("modules"), dict) else {}
    ov = _overview_identity(modules)

    kickoff = None
    match_time = str(ov.get("matchTime") or "").strip()
    if match_time:
        try:
            kickoff = beijing_str_to_utc(match_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            kickoff = None

    match_info = MatchInfo(
        source="collector_v2",
        external_match_id=ext_id,
        competition=str(ov.get("tournamentName") or "").strip() or None,
        home_team=str(ov.get("homeTeamName") or "").strip() or None,
        away_team=str(ov.get("awayTeamName") or "").strip() or None,
        kickoff_at=kickoff,
        kickoff_known=kickoff is not None,
        match_status=str(ov.get("matchStatus") or "").strip() or None,
        sporttery_no=None,
    )

    # 采集时间：优先轮次结束时间，其次开始时间；均缺失交由主流程按规则处理。
    collected_at = _parse_iso_utc(raw.get("finishedAt") or raw.get("startedAt"))

    cm = CanonicalMatch(
        source="collector_v2",
        external_match_id=ext_id,
        match=match_info,
        raw_payload=raw,
        collected_at=collected_at,
    )

    missing = set(raw.get("missingModules") or [])
    for name in _MODULES:
        items = modules.get(name)
        if name in missing or not isinstance(items, list) or not items:
            cm.module_states.append(ModuleState(name=name, quality=QUALITY_NOT_COLLECTED))
            continue
        valid_items = [i for i in items if isinstance(i, dict) and i.get("valid")]
        if not valid_items:
            cm.module_states.append(ModuleState(
                name=name, quality=QUALITY_FETCH_FAILED, fetch_status="all_responses_invalid",
            ))
        else:
            cm.module_states.append(ModuleState(
                name=name, quality=QUALITY_COMPLETE, fetch_status="ok",
                detail=f"{len(valid_items)}/{len(items)} valid responses",
            ))

    # 如实记录：盘口公司列表本版仅保留原文，不写业务赔率表。
    if isinstance(raw.get("unclassifiedOdds"), list) and raw["unclassifiedOdds"]:
        cm.module_states.append(ModuleState(
            name="odds_bookmaker_lists",
            quality=QUALITY_NOT_COLLECTED,
            detail=(
                "bookmaker odds lists retained as raw payload only; "
                "column semantics unconfirmed, not written to odds tables"
            ),
        ))
    return cm
