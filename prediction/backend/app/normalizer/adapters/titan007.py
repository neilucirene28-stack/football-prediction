# titan007.py — Titan007 可读版 JSON 适配器（事务边界与时区修正版）
import logging
import re
from datetime import datetime
from typing import Any

from ..canonical import (
    AsiaOddsLine,
    BEIJING_TZ,
    CanonicalMatch,
    CornerOddsLine,
    MatchInfo,
    ModuleState,
    TableBlock,
    UnparsedBlock,
    beijing_str_to_utc,
)

logger = logging.getLogger("football-prediction-api")


_MODULE_KEY_MAP = {
    "分析": "analysis",
    "亚让": "asia",
    "胜平负": "europe",
    "角球": "corners",
    "总进球": "over_under",
    "现场分析": "live_analysis",
}

# 数据状态中文值到质量状态的映射（已抓取由内容阶段细化，此处跳过）
_FETCH_STATE_MAP = {
    "已抓取": None,
    "缺失": "not_collected",
}

_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
)

_FLATTENED_MAX_KEYS = 15
_SUFFIX_RE = re.compile(r".+_\d+$")


def _parse_bj_utc(dt_str: str) -> datetime:
    """解析北京时间字符串为 timezone-aware UTC datetime。

    兼容多种格式。无法解析时抛 ValueError。
    例：2026-09-20 19:57 北京时间 → 2026-09-20 11:57 UTC
    """
    for fmt in _DT_FORMATS:
        try:
            return beijing_str_to_utc(dt_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable datetime: {dt_str!r}")


def _float_or_none(v: Any):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _looks_flattened(data: Any) -> bool:
    """判断表格数据是否被扁平化（单元格值作 key，行/列语义丢失）。"""
    if not isinstance(data, list) or not data:
        return False
    first = data[0]
    if not isinstance(first, dict):
        return False
    keys = list(first.keys())
    if len(keys) > _FLATTENED_MAX_KEYS:
        return True
    return any(_SUFFIX_RE.match(k) for k in keys)


def _parse_asia_lines(node: dict, captured_at: datetime) -> list:
    lines = []
    # 公司盘口：初盘与即时盘分离（observed_at 用采集时间）
    for row in node.get("公司盘口", []):
        company = row.get("公司")
        if not company:
            continue
        if row.get("初盘盘口") is not None:
            lines.append(AsiaOddsLine(
                bookmaker=company,
                handicap_text=row.get("初盘盘口"),
                home_water=_float_or_none(row.get("初盘主水")),
                away_water=_float_or_none(row.get("初盘客水")),
                is_opening=True,
                observed_at=captured_at,
                time_semantics="captured_at",
                raw=dict(row),
            ))
        if row.get("即时盘口") is not None:
            lines.append(AsiaOddsLine(
                bookmaker=company,
                handicap_text=row.get("即时盘口"),
                home_water=_float_or_none(row.get("即时主水")),
                away_water=_float_or_none(row.get("即时客水")),
                is_opening=False,
                observed_at=captured_at,
                time_semantics="captured_at",
                raw=dict(row),
            ))
    # 历史变化：observed_at 用实际变化时间（北京时间→UTC）
    for row in node.get("历史变化", []):
        company = row.get("公司")
        if not company:
            continue
        date_str = row.get("日期", "")
        time_str = row.get("时间", "")
        observed = None
        if date_str and time_str:
            try:
                observed = _parse_bj_utc(f"{date_str} {time_str}")
            except ValueError:
                observed = None
        lines.append(AsiaOddsLine(
            bookmaker=company,
            handicap_text=row.get("盘口"),
            home_water=_float_or_none(row.get("主水")),
            away_water=_float_or_none(row.get("客水")),
            is_opening=None,
            observed_at=observed,
            time_semantics="change_time" if observed else "unknown",
            raw=dict(row),
        ))
    return lines


def _parse_corner_lines(node: dict, captured_at: datetime) -> list:
    """角球盘口：初盘与即时盘分开为两条记录。

    主水/客水与 over/under 语义未经业务确认，仅保留在 canonical。
    """
    lines = []
    for row in node.get("公司盘口", []):
        company = row.get("公司")
        if not company:
            continue
        for prefix, is_opening in (("初盘", True), ("即时", False)):
            line_text = row.get(f"{prefix}盘口")
            line_val = None
            err = None
            try:
                if line_text is not None:
                    line_val = float(line_text)
            except (TypeError, ValueError):
                err = f"cannot parse corner line: {line_text!r}"
            lines.append(CornerOddsLine(
                bookmaker=company,
                line=line_val,
                line_text=line_text,
                is_opening=is_opening,
                observed_at=captured_at,
                time_semantics="captured_at",
                home_water=_float_or_none(row.get(f"{prefix}主水")),
                away_water=_float_or_none(row.get(f"{prefix}客水")),
                parse_error=err,
                raw=dict(row),
            ))
    return lines


def _parse_analysis_tables(tables: dict) -> list:
    """逐表格编号检测，返回 TableBlock 列表。"""
    blocks = []
    for table_name, entries in tables.items():
        if not entries:
            blocks.append(TableBlock(table_name=table_name, table_no=-1, quality="empty"))
            continue
        for entry in entries:
            table_no = entry.get("表格编号", -1) if isinstance(entry, dict) else -1
            data = entry.get("数据") if isinstance(entry, dict) else None
            if data is None or (isinstance(data, list) and not data):
                blocks.append(TableBlock(
                    table_name=table_name, table_no=table_no, quality="empty",
                    raw=entry,
                ))
            elif _looks_flattened(data):
                blocks.append(TableBlock(
                    table_name=table_name, table_no=table_no, quality="parse_error",
                    parse_error="table_flattened_anomaly",
                    raw=entry,
                ))
            else:
                blocks.append(TableBlock(
                    table_name=table_name, table_no=table_no, quality="ok",
                    rows=data,
                    raw=entry,
                ))
    return blocks


def adapt_titan007(raw: dict) -> CanonicalMatch:
    """将 Titan007 可读版 JSON 解析为 CanonicalMatch。

    已知限制（如实反映）:
    - 开赛时间无明确字段，kickoff_at=None，kickoff_known=False
    - 联赛名仅存在于比赛名称长文本中，不做猜测提取
    - 分类表格逐表格编号检测：正常/空/扁平化异常分别标记
    """
    raw = raw.get("data", raw)  # 兼容 raw_payload 包装

    info = raw.get("比赛信息", {})
    states = raw.get("数据状态", {})
    ext_id = str(info.get("比赛ID", "")).strip()

    match_info = MatchInfo(
        source="titan007",
        external_match_id=ext_id,
        competition=None,
        home_team=None,
        away_team=None,
        kickoff_at=None,
        kickoff_known=False,
    )

    cm = CanonicalMatch(
        source="titan007",
        external_match_id=ext_id,
        match=match_info,
        raw_payload=raw,
    )

    # 数据状态 → 模块质量标记（仅 not_collected；已抓取由内容阶段细化）
    for zh_name, state_str in states.items():
        quality = _FETCH_STATE_MAP.get(state_str)
        if quality is None:
            continue
        internal = _MODULE_KEY_MAP.get(zh_name, zh_name)
        cm.module_states.append(ModuleState(
            name=internal, quality=quality, fetch_status=state_str,
        ))

    # 采集时间（亚让模块的采集时间作为样本基准）
    captured_raw = raw.get("亚让", {}).get("采集时间")
    captured_at = None
    if captured_raw:
        try:
            captured_at = _parse_bj_utc(captured_raw)
        except ValueError:
            captured_at = None
    cm.collected_at = captured_at

    # 亚让
    asia = raw.get("亚让", {})
    if isinstance(asia, dict) and asia.get("公司盘口"):
        cm.asia_odds = _parse_asia_lines(asia, captured_at)

    # 角球（初盘/即时分开，主水/客水语义待确认仅保留 canonical）
    corners = raw.get("角球", {})
    if isinstance(corners, dict) and corners.get("公司盘口"):
        cm.corner_odds = _parse_corner_lines(corners, captured_at)

    # 胜平负：已抓取但数据为空 → 状态与有效数据不一致
    europe = raw.get("胜平负", {})
    if isinstance(europe, dict):
        company_odds = europe.get("公司指数")
        if isinstance(company_odds, list) and not company_odds:
            cm.module_states.append(ModuleState(
                name="europe",
                quality="empty",
                fetch_status="已抓取",
                detail="fetch_ok_but_data_empty: company index list is empty",
            ))

    # 分析.分类表格：逐表格编号检测
    analysis = raw.get("分析", {})
    tables = analysis.get("分类表格", {})
    if isinstance(tables, dict):
        cm.table_blocks = _parse_analysis_tables(tables)
        for tb in cm.table_blocks:
            if tb.quality == "parse_error":
                cm.unparsed_blocks.append(UnparsedBlock(
                    block_name=f"analysis_table.{tb.table_name}.{tb.table_no}",
                    parse_error=tb.parse_error,
                    raw=tb.raw,
                ))
                cm.module_states.append(ModuleState(
                    name=f"analysis_table.{tb.table_name}.{tb.table_no}",
                    quality="parse_error",
                    parse_status="table_flattened_anomaly",
                    detail="original preserved, no column guessing",
                ))
            elif tb.quality == "empty":
                cm.module_states.append(ModuleState(
                    name=f"analysis_table.{tb.table_name}.{tb.table_no}",
                    quality="empty",
                ))

    return cm
