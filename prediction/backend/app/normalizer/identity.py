# identity.py — Normalizer V1 身份解析（比赛/球队匹配，含跨来源候选匹配）
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("football-prediction-api")

# 开赛时间安全窗口（±2 小时）
_KICKOFF_WINDOW = timedelta(hours=2)


class IdentityError(Exception):
    pass


class IdentityResult:
    def __init__(self, action: str, match_id: str | None = None, team_ids: dict | None = None, pending_reason: str | None = None):
        self.action = action          # created / linked / pending_review
        self.match_id = match_id
        self.team_ids = team_ids or {}
        self.pending_reason = pending_reason

    def as_dict(self) -> dict:
        return {
            "action": self.action,
            "match_id": self.match_id,
            "team_ids": self.team_ids,
            "pending_reason": self.pending_reason,
        }


def _ensure_team(conn, source: str, team_name: str) -> str:
    """确保球队存在：先查别名，未命中则创建 teams + alias。

    使用 INSERT ... ON CONFLICT DO NOTHING 而非 try/except pass，
    避免 PostgreSQL 唯一约束错误让当前事务进入 aborted 状态。
    """
    if not team_name:
        raise IdentityError("empty team name")

    # 1. 查别名
    cur = conn.execute(
        """
        SELECT team_id FROM team_aliases
        WHERE source = %s AND alias_name = %s
        LIMIT 1
        """,
        (source, team_name),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # 2. 查 canonical_name 精确匹配（跨 source 共享同一球队）
    cur = conn.execute(
        """
        SELECT id FROM teams WHERE canonical_name = %s
        LIMIT 1
        """,
        (team_name,),
    )
    row = cur.fetchone()
    if row:
        team_id = row[0]
    else:
        team_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO teams (id, canonical_name)
            VALUES (%s, %s)
            """,
            (team_id, team_name),
        )

    # 3. 写别名：ON CONFLICT DO NOTHING 避免唯一约束异常进入 aborted
    conn.execute(
        """
        INSERT INTO team_aliases (team_id, source, alias_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (source, alias_name) DO NOTHING
        """,
        (team_id, source, team_name),
    )

    return team_id


def _find_candidates_by_teams_and_kickoff(
    conn,
    home_team: str,
    away_team: str,
    kickoff_at: datetime,
) -> list:
    """基于主客队名与开赛时间窗口查询候选比赛。

    实际 SQL（跨来源匹配核心）：
    通过 teams 表 join matches，按主客队 canonical_name 精确匹配，
    开赛时间使用 ±2 小时安全窗口（处理不同来源的时间误差）。

    返回 match_id 列表。
    """
    window_start = kickoff_at - _KICKOFF_WINDOW
    window_end = kickoff_at + _KICKOFF_WINDOW
    cur = conn.execute(
        """
        SELECT m.id
        FROM matches m
        JOIN teams th ON m.home_team_id = th.id
        JOIN teams ta ON m.away_team_id = ta.id
        WHERE th.canonical_name = %s
          AND ta.canonical_name = %s
          AND m.kickoff_at >= %s
          AND m.kickoff_at <= %s
        LIMIT 5
        """,
        (home_team, away_team, window_start, window_end),
    )
    return [r[0] for r in cur.fetchall()]


def resolve_match(conn, cm) -> IdentityResult:
    """解析统一比赛对象 → matches 表记录。

    优先级：
    1. source + external_match_id 精确匹配（最高）→ linked
    2. home_team + away_team + kickoff_at（±2h 窗口）跨来源候选匹配：
       - 恰好 1 个候选 → linked（跨来源关联）
       - 0 个候选 → created（新建比赛）
       - 多个候选 → pending_review（不自动合并）
    3. 开赛时间未知 → pending_review
    """
    source = cm.source
    ext_id = cm.external_match_id

    # 1. source + external_match_id 精确匹配（最高优先级）
    cur = conn.execute(
        """
        SELECT match_id FROM match_source_ids
        WHERE source = %s AND external_match_id = %s
        LIMIT 1
        """,
        (source, ext_id),
    )
    row = cur.fetchone()
    if row:
        return IdentityResult("linked", match_id=row[0])

    # 2. 开赛时间未知 → pending_review
    kickoff = cm.match.kickoff_at
    kickoff_known = cm.match.kickoff_known and kickoff is not None

    # 3. 跨来源候选匹配（需要开赛时间已知 + 主客队名已知）
    candidates = []
    if kickoff_known and cm.match.home_team and cm.match.away_team:
        candidates = _find_candidates_by_teams_and_kickoff(
            conn, cm.match.home_team, cm.match.away_team, kickoff,
        )
        if len(candidates) == 1:
            # 恰好 1 个候选：跨来源关联
            match_id = candidates[0]
            _ensure_match_source_id(
                conn, source, ext_id,
                cm.match.external_match_id2, match_id,
            )
            return IdentityResult("linked", match_id=match_id)
        if len(candidates) > 1:
            # 多候选：不自动合并
            return IdentityResult(
                "pending_review",
                pending_reason=(
                    f"multiple candidate matches ({len(candidates)}) "
                    f"for source={source} ext_id={ext_id}"
                ),
            )
        # 0 个候选：走创建流程
    elif not kickoff_known:
        return IdentityResult(
            "pending_review",
            pending_reason=(
                f"kickoff_at unknown for source={source} ext_id={ext_id}, "
                f"cannot match cross-source"
            ),
        )

    # 4. 新比赛：创建球队与比赛
    team_ids = {}
    match_id = str(uuid.uuid4())
    home_id = _ensure_team(conn, source, cm.match.home_team)
    away_id = _ensure_team(conn, source, cm.match.away_team)
    team_ids["home"] = home_id
    team_ids["away"] = away_id

    conn.execute(
        """
        INSERT INTO matches
        (id, competition, season, sporttery_no, kickoff_at,
         home_team_id, away_team_id, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled')
        """,
        (
            match_id,
            cm.match.competition,
            None,
            cm.match.sporttery_no,
            kickoff,
            home_id,
            away_id,
        ),
    )

    _ensure_match_source_id(conn, source, ext_id, cm.match.external_match_id2, match_id)

    return IdentityResult("created", match_id=match_id, team_ids=team_ids)


def _ensure_match_source_id(conn, source: str, ext_id: str, ext_id2: str | None, match_id: str) -> None:
    """记录来源比赛 ID 映射（幂等：已存在则跳过）"""
    cur = conn.execute(
        """
        SELECT 1 FROM match_source_ids
        WHERE source = %s AND external_match_id = %s
        LIMIT 1
        """,
        (source, ext_id),
    )
    if cur.fetchone():
        return
    conn.execute(
        """
        INSERT INTO match_source_ids
        (match_id, source, external_match_id, external_match_id2)
        VALUES (%s, %s, %s, %s)
        """,
        (match_id, source, ext_id, ext_id2),
    )
