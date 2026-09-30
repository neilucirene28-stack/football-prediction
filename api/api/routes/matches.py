"""比赛列表 / 详情。有 DB 读 DB，无 DB 用演示数据。"""
from fastapi import APIRouter, HTTPException

from ..db import get_conn
from ..demo import demo_matches

router = APIRouter(prefix="/api/matches", tags=["matches"])


def _from_db():
    conn = get_conn()
    if conn is None:
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """SELECT m.*, json_agg(o) FILTER (WHERE o.id IS NOT NULL) AS odds_rows
                   FROM matches m LEFT JOIN odds_snapshots o ON o.match_id = m.id
                   WHERE m.status = 'scheduled' AND m.kickoff_at > now()
                   GROUP BY m.id ORDER BY m.kickoff_at LIMIT 100""")
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return None


@router.get("")
def list_matches():
    rows = _from_db()
    if rows is not None:
        return {"source": "db", "matches": rows}
    return {"source": "demo", "matches": demo_matches()}


@router.get("/{match_id}")
def match_detail(match_id: int):
    rows = _from_db()
    if rows is not None:
        for r in rows:
            if r["id"] == match_id:
                return r
        raise HTTPException(404, "比赛不存在")
    for m in demo_matches():
        if m["id"] == match_id:
            return m
    raise HTTPException(404, "比赛不存在")
