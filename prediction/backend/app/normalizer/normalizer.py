# normalizer.py — Normalizer V1 主流程
# 流程：collection_snapshot(显式 ID) → adapter → canonical → identity → match_snapshot → odds
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from ..db import get_pool
from .adapters.titan007 import adapt_titan007
from .adapters.xiaodianhuo import adapt_xiaodianhuo_batch
from .adapters.collector_v2 import adapt_collector_v2
from .canonical import AsiaOddsLine, CanonicalMatch, CornerOddsLine, SportteryOdds
from .identity import resolve_match

logger = logging.getLogger("football-prediction-api")


_ADAPTERS = {
    "titan007": ("single", adapt_titan007),
    "xiaodianhuo": ("batch", adapt_xiaodianhuo_batch),
    "collector_v2": ("single", adapt_collector_v2),
}


class NormalizerError(Exception):
    pass


class NormalizeResult:
    def __init__(self, source: str, external_match_id: str, action: str,
                 match_id: str | None = None, collection_snapshot_id: str | None = None,
                 match_snapshot_id: str | None = None,
                 asia_lines: int = 0, corner_lines: int = 0, sporttery_lines: int = 0,
                 pending_reason: str | None = None):
        self.source = source
        self.external_match_id = external_match_id
        self.action = action
        self.match_id = match_id
        self.collection_snapshot_id = collection_snapshot_id
        self.match_snapshot_id = match_snapshot_id
        self.asia_lines = asia_lines
        self.corner_lines = corner_lines
        self.sporttery_lines = sporttery_lines
        self.pending_reason = pending_reason

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "external_match_id": self.external_match_id,
            "action": self.action,
            "match_id": self.match_id,
            "collection_snapshot_id": self.collection_snapshot_id,
            "match_snapshot_id": self.match_snapshot_id,
            "asia_lines": self.asia_lines,
            "corner_lines": self.corner_lines,
            "sporttery_lines": self.sporttery_lines,
            "pending_reason": self.pending_reason,
        }


def _content_fingerprint(*parts) -> str:
    """盘口内容指纹（append-only + 应用层去重）"""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fetch_collection_snapshot(collection_snapshot_id: str) -> dict:
    """显式获取 collection_snapshots 行（含采集时间与 run_id），不靠最近推断。"""
    pool = get_pool()
    with pool.connection() as conn:
        cur = conn.execute(
            """
            SELECT id, run_id, source, entity_type, external_id,
                   raw_payload, captured_at
            FROM collection_snapshots
            WHERE id = %s
            """,
            (collection_snapshot_id,),
        )
        row = cur.fetchone()
        if not row:
            raise NormalizerError(
                f"collection_snapshot not found: {collection_snapshot_id}"
            )
    return {
        "id": row[0],
        "run_id": row[1],
        "source": row[2],
        "entity_type": row[3],
        "external_id": row[4],
        "raw_payload": row[5],
        "captured_at": row[6],
    }


def _create_match_snapshot(
    conn,
    collection_snapshot_id: str,
    collection_run_id: str | None,
    match_id: str,
    source: str,
    external_match_id: str,
    collected_at: datetime,
    raw_payload: dict,
    completeness: dict,
) -> str:
    """创建 match_snapshots 行，返回 match_snapshot_id。

    collection_run_id 来源于 collection_snapshot.run_id（显式保留）。
    collection_snapshot_id / external_match_id / completeness 写入
    normalized_payload（原始快照 ID 持久化追溯 + 业务信息）。
    """
    ms_id = str(uuid.uuid4())
    normalized = {
        "_trace": {
            "collection_snapshot_id": collection_snapshot_id,
            "external_match_id": external_match_id,
        },
        "completeness": completeness,
    }
    conn.execute(
        """
        INSERT INTO match_snapshots
        (id, match_id, collection_run_id, source,
         collected_at, data_status, completeness, raw_payload, normalized_payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
        """,
        (
            ms_id, match_id, collection_run_id, source,
            collected_at, "partial",
            json.dumps(completeness, ensure_ascii=False, default=str),
            json.dumps(raw_payload, ensure_ascii=False, default=str),
            json.dumps(normalized, ensure_ascii=False, default=str),
        ),
    )
    return ms_id


def _upsert_odds_asia(
    conn, match_id: str, match_snapshot_id: str, source: str,
    collected_at: datetime, line: AsiaOddsLine,
) -> bool:
    """写入一条亚让盘口（append-only + 内容指纹去重）。

    返回 True 表示新写入，False 表示指纹重复跳过。
    注意：odds_asia 无 source 列，来源保留在 raw_payload._ingest.source。
    """
    fp = _content_fingerprint(
        source, line.bookmaker, line.handicap_text,
        line.home_water, line.away_water, line.is_opening,
        line.observed_at.isoformat() if line.observed_at else "",
    )
    cur = conn.execute(
        """
        SELECT 1 FROM odds_asia
        WHERE raw_payload->>'_fp' = %s AND match_id = %s
        LIMIT 1
        """,
        (fp, match_id),
    )
    if cur.fetchone():
        return False
    conn.execute(
        """
        INSERT INTO odds_asia
        (id, match_id, snapshot_id, bookmaker, handicap_text,
         home_water, away_water, is_opening,
         observed_at, raw_payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            str(uuid.uuid4()), match_id, match_snapshot_id,
            line.bookmaker, line.handicap_text,
            line.home_water, line.away_water, line.is_opening,
            line.observed_at if line.observed_at else collected_at,
            json.dumps({
                "_fp": fp,
                "_ingest": {"source": source, "captured_at": collected_at.isoformat()},
                "time_semantics": line.time_semantics,
                "raw": line.raw,
            }, ensure_ascii=False, default=str),
        ),
    )
    return True


def _upsert_odds_corners(
    conn, match_id: str, match_snapshot_id: str, source: str,
    collected_at: datetime, line: CornerOddsLine,
) -> bool:
    """角球盘口入库（当前暂停写入）。

    语义说明：Titan007 角球的主水/客水与 over/under 语义未经业务确认。
    当前阶段决策：角球数据照常解析，原始内容已保留在比赛快照
    （match_snapshots.raw_payload）中供展示，但暂停写入 odds_corners 正式表；
    不影响亚盘/竞彩等其他已确认数据的正常入库，也不将比赛标为 pending_review。
    主水/客水语义确认后，删除下方守卫即可恢复正式入库。
    """
    # 角球正式入库暂停（已定方案）：跳过写入，仅记录日志，返回 False 表示未写入
    logger.info(
        "corners ingest paused source=%s bookmaker=%s line=%s "
        "(raw preserved in match snapshot)",
        source, line.bookmaker, line.line,
    )
    return False
    if line.parse_error:
        logger.warning(
            "normalize corner parse_error source=%s bookmaker=%s err=%s",
            source, line.bookmaker, line.parse_error,
        )
        return False
    fp = _content_fingerprint(
        source, line.bookmaker, line.line,
        line.is_opening,
        line.observed_at.isoformat() if line.observed_at else "",
    )
    cur = conn.execute(
        """
        SELECT 1 FROM odds_corners
        WHERE raw_payload->>'_fp' = %s AND match_id = %s
        LIMIT 1
        """,
        (fp, match_id),
    )
    if cur.fetchone():
        return False
    conn.execute(
        """
        INSERT INTO odds_corners
        (id, match_id, snapshot_id, bookmaker, line,
         is_opening, observed_at, raw_payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            str(uuid.uuid4()), match_id, match_snapshot_id,
            line.bookmaker, line.line,
            line.is_opening,
            line.observed_at if line.observed_at else collected_at,
            json.dumps({
                "_fp": fp,
                "_ingest": {"source": source, "captured_at": collected_at.isoformat()},
                "time_semantics": line.time_semantics,
                "home_water_semantics_unconfirmed": line.home_water,
                "away_water_semantics_unconfirmed": line.away_water,
                "raw": line.raw,
            }, ensure_ascii=False, default=str),
        ),
    )
    return True


def _upsert_odds_sporttery(
    conn, match_id: str, match_snapshot_id: str, source: str,
    collected_at: datetime, odds: SportteryOdds,
) -> bool:
    """写入一条竞彩胜平负赔率（odds_sporttery.observed_at NOT NULL）。"""
    observed = odds.observed_at or collected_at
    fp = _content_fingerprint(
        source, odds.market, odds.home_odds, odds.draw_odds,
        odds.away_odds, observed.isoformat(),
    )
    cur = conn.execute(
        """
        SELECT 1 FROM odds_sporttery
        WHERE raw_payload->>'_fp' = %s AND match_id = %s
        LIMIT 1
        """,
        (fp, match_id),
    )
    if cur.fetchone():
        return False
    conn.execute(
        """
        INSERT INTO odds_sporttery
        (id, match_id, snapshot_id, market,
         home_odds, draw_odds, away_odds,
         observed_at, raw_payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            str(uuid.uuid4()), match_id, match_snapshot_id,
            odds.market, odds.home_odds, odds.draw_odds, odds.away_odds,
            observed,
            json.dumps({
                "_fp": fp,
                "_ingest": {"source": source, "captured_at": collected_at.isoformat()},
                "raw": odds.raw,
            }, ensure_ascii=False, default=str),
        ),
    )
    return True


def _normalize_single(
    conn,
    cm: CanonicalMatch,
    collection_snapshot_id: str,
    collection_run_id: str | None,
    collection_captured_at: datetime | None = None,
) -> NormalizeResult:
    """处理单个 CanonicalMatch：
    identity → match_snapshot 创建 → 盘口入库（全部显式 snapshot_id 关联）
    """
    # 采集时间回退：优先使用 collection_snapshots.captured_at（真实采集时间），
    # 回退到数据内 capturedAt；禁止使用 Normalizer 执行时刻的 now()
    collected_at = collection_captured_at or cm.collected_at

    # 采集时间双重缺失保护（方案 A）：都缺失时拒绝入库
    if collected_at is None:
        logger.warning(
            "normalize pending_review source=%s ext_id=%s reason=collected_at missing",
            cm.source, cm.external_match_id,
        )
        return NormalizeResult(
            cm.source, cm.external_match_id, "pending_review",
            collection_snapshot_id=collection_snapshot_id,
            pending_reason=(
                "collected_at missing: collection_captured_at is None "
                "and entry capturedAt is missing"
            ),
        )

    # 1. 身份解析
    ident = resolve_match(conn, cm)
    if ident.action == "pending_review":
        logger.info(
            "normalize pending_review source=%s ext_id=%s reason=%s",
            cm.source, cm.external_match_id, ident.pending_reason,
        )
        return NormalizeResult(
            cm.source, cm.external_match_id, "pending_review",
            collection_snapshot_id=collection_snapshot_id,
            pending_reason=ident.pending_reason,
        )

    match_id = ident.match_id

    # Retry-safe: the very same uploaded snapshot must not create another
    # match_snapshot or repeat its odds on a second normalization attempt.
    prior = conn.execute(
        """SELECT id FROM match_snapshots
           WHERE match_id = %s
             AND normalized_payload #>> '{_trace,collection_snapshot_id}' = %s
             AND normalized_payload #>> '{_trace,external_match_id}' = %s
           LIMIT 1""",
        (match_id, collection_snapshot_id, cm.external_match_id),
    ).fetchone()
    if prior:
        return NormalizeResult(
            cm.source, cm.external_match_id, "already_normalized",
            match_id=match_id, collection_snapshot_id=collection_snapshot_id,
            match_snapshot_id=str(prior[0]),
        )

    # 2. 构建 completeness 摘要
    completeness = {}
    for ms in cm.module_states:
        completeness[ms.name] = ms.quality

    # 3. 创建 match_snapshot（显式关联 collection_run_id，并持久化追溯与业务信息）
    match_snapshot_id = _create_match_snapshot(
        conn, collection_snapshot_id, collection_run_id,
        match_id, cm.source, cm.external_match_id,
        collected_at, cm.raw_payload, completeness,
    )

    # 4. 盘口入库（显式 match_snapshot_id 关联，不用 ORDER BY 猜测）
    asia_count = 0
    corner_count = 0
    sporttery_count = 0
    for line in cm.asia_odds:
        if _upsert_odds_asia(conn, match_id, match_snapshot_id, cm.source, collected_at, line):
            asia_count += 1
    for line in cm.corner_odds:
        if _upsert_odds_corners(conn, match_id, match_snapshot_id, cm.source, collected_at, line):
            corner_count += 1
    for odds in cm.sporttery_odds:
        if _upsert_odds_sporttery(conn, match_id, match_snapshot_id, cm.source, collected_at, odds):
            sporttery_count += 1

    logger.info(
        "normalize done source=%s ext_id=%s action=%s "
        "collection_snapshot=%s match_snapshot=%s "
        "asia=%d corner=%d sporttery=%d",
        cm.source, cm.external_match_id, ident.action,
        collection_snapshot_id, match_snapshot_id,
        asia_count, corner_count, sporttery_count,
    )
    return NormalizeResult(
        cm.source, cm.external_match_id, ident.action,
        match_id=match_id,
        collection_snapshot_id=collection_snapshot_id,
        match_snapshot_id=match_snapshot_id,
        asia_lines=asia_count, corner_lines=corner_count,
        sporttery_lines=sporttery_count,
    )


def normalize_collection_snapshot(collection_snapshot_id: str) -> list:
    """主入口：显式传入 collection_snapshot_id，执行完整 Normalizer 流程。

    流程：
    1. 用 collection_snapshot_id 查询 collection_snapshots（不靠最近推断）
    2. adapter 解析 raw_payload → canonical（可能多场）
    3. identity 解析身份（含跨来源候选匹配）
    4. 创建 match_snapshots（回填 collection_run_id）
    5. 盘口入库（显式 match_snapshot_id 关联）

    返回 NormalizeResult 列表。
    """
    snap = _fetch_collection_snapshot(collection_snapshot_id)
    source = snap["source"]

    if source not in _ADAPTERS:
        raise NormalizerError(f"unsupported source: {source}")
    mode, adapter_fn = _ADAPTERS[source]

    raw_payload = snap["raw_payload"]
    if isinstance(raw_payload, str):
        raw_payload = json.loads(raw_payload)
    # 按约定快照结构解包：顶层同时含 _ingest（元信息）与 data（业务数据）
    if isinstance(raw_payload, dict) and "_ingest" in raw_payload:
        if "data" not in raw_payload:
            raise NormalizerError(
                f"collection_snapshot {collection_snapshot_id}: "
                f"payload has _ingest but missing data"
            )
        raw_payload = raw_payload["data"]

    if mode == "batch":
        cms = adapter_fn(raw_payload)
    else:
        cms = [adapter_fn(raw_payload)]

    # 解包/校验完成后才获取数据库连接池（解包是纯数据操作，不应依赖 DB）
    pool = get_pool()
    results = []
    with pool.connection() as conn:
        with conn.transaction():
            for cm in cms:
                results.append(_normalize_single(
                    conn, cm, collection_snapshot_id, snap["run_id"],
                    collection_captured_at=snap["captured_at"],
                ))

    return results
