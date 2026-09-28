# canonical.py — Normalizer V1 统一比赛对象结构
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional


# 数据质量状态（6 态）
QUALITY_NOT_COLLECTED = "not_collected"
QUALITY_FETCH_FAILED = "fetch_failed"
QUALITY_EMPTY = "empty"
QUALITY_PARSE_ERROR = "parse_error"
QUALITY_PARTIAL = "partial"
QUALITY_COMPLETE = "complete"

# 北京时间时区（Titan007 / 小店欢样本时间均为北京时间）
BEIJING_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def beijing_to_utc(dt: datetime) -> datetime:
    """将 naive 的北京时间 datetime 附加 Asia/Shanghai 时区并转为 UTC。

    例如：2026-09-20 19:57（北京时间）→ 2026-09-20 11:57 UTC
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BEIJING_TZ)
    return dt.astimezone(timezone.utc)


def beijing_str_to_utc(dt_str: str, fmt: str) -> datetime:
    """解析北京时间字符串为 timezone-aware UTC datetime。"""
    return beijing_to_utc(datetime.strptime(dt_str, fmt))


@dataclass
class ModuleState:
    """单个数据模块的采集与解析状态"""
    name: str
    quality: str                      # 上述 6 态之一
    fetch_status: Optional[str] = None
    fetch_reason: Optional[str] = None
    parse_status: Optional[str] = None
    detail: Optional[str] = None


@dataclass
class TableBlock:
    """分类表格中单个表格（按表格编号）的解析结果"""
    table_name: str
    table_no: int
    quality: str                      # ok / empty / parse_error
    rows: Any = None                  # 正常时保留行数据（list）
    parse_error: Optional[str] = None
    raw: Any = None


@dataclass
class MatchInfo:
    """比赛基本信息（canonical）"""
    source: str
    external_match_id: str
    external_match_id2: Optional[str] = None
    competition: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    kickoff_at: Optional[datetime] = None   # timezone-aware UTC
    kickoff_known: bool = False
    match_status: Optional[str] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    sporttery_no: Optional[str] = None


@dataclass
class AsiaOddsLine:
    """单条亚让盘口记录"""
    bookmaker: str
    handicap_text: Optional[str] = None
    home_water: Optional[float] = None
    away_water: Optional[float] = None
    is_opening: Optional[bool] = None       # True=初盘 False=即时 None=历史
    observed_at: Optional[datetime] = None  # timezone-aware UTC
    time_semantics: str = "captured_at"     # change_time / captured_at
    raw: dict = field(default_factory=dict)


@dataclass
class CornerOddsLine:
    """单条角球盘口记录（初盘与即时盘分开）

    语义说明：Titan007 角球的主水/客水是让球盘口两侧水位，
    与 odds_corners.over_odds/under_odds 的大小球语义是否一致
    未经业务确认，本版仅保留在 canonical/raw_payload。
    """
    bookmaker: str
    line: Optional[float] = None            # numeric(5,2)
    line_text: Optional[str] = None
    is_opening: Optional[bool] = None       # True=初盘 False=即时
    observed_at: Optional[datetime] = None
    time_semantics: str = "captured_at"
    # 语义待确认字段：只入 canonical/raw_payload，不写 odds_corners 列
    home_water: Optional[float] = None
    away_water: Optional[float] = None
    parse_error: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class SportteryOdds:
    """竞彩官方胜平负赔率"""
    market: str                             # wdl / nwdl
    home_odds: Optional[float] = None       # odds['3']
    draw_odds: Optional[float] = None       # odds['1']
    away_odds: Optional[float] = None       # odds['0']
    observed_at: Optional[datetime] = None
    raw: dict = field(default_factory=dict)


@dataclass
class UnparsedBlock:
    """无法语义解析的数据块（原文保留）"""
    block_name: str                         # 如 analysis_table.近期战绩.51
    parse_error: str                        # 如 table_flattened_anomaly
    raw: Any


@dataclass
class CanonicalMatch:
    """统一比赛对象：一次适配的输出"""
    source: str
    external_match_id: str
    match: MatchInfo
    module_states: list = field(default_factory=list)          # list[ModuleState]
    asia_odds: list = field(default_factory=list)              # list[AsiaOddsLine]
    corner_odds: list = field(default_factory=list)            # list[CornerOddsLine]
    sporttery_odds: list = field(default_factory=list)         # list[SportteryOdds]
    table_blocks: list = field(default_factory=list)           # list[TableBlock]（逐表格编号）
    unparsed_blocks: list = field(default_factory=list)        # list[UnparsedBlock]
    fifa_rank: dict = field(default_factory=dict)
    raw_payload: dict = field(default_factory=dict)
    collected_at: Optional[datetime] = None                    # 采集快照时间（timezone-aware）
