"""复盘接口：聚合 backtest_results，没有数据时返回口径说明。"""
from fastapi import APIRouter

from engine.backtest import calibration_table
from ..db import get_conn

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("/summary")
def summary():
    conn = get_conn()
    if conn is None:
        return {"status": "no_data",
                "note": "未连接数据库。复盘需要已完赛的比赛与赛前预测快照。"}
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT p_home, p_draw, p_away, outcome, brier, logloss
                           FROM backtest_results ORDER BY evaluated_at DESC LIMIT 2000""")
            rows = cur.fetchall()
    except Exception as e:
        return {"status": "error", "note": str(e)}
    if not rows:
        return {"status": "no_data", "note": "暂无复盘记录"}
    n = len(rows)
    data = [((r["p_home"], r["p_draw"], r["p_away"]), r["outcome"]) for r in rows]
    return {
        "status": "ok", "n": n,
        "brier": round(sum(r["brier"] for r in rows) / n, 4),
        "logloss": round(sum(r["logloss"] for r in rows) / n, 4),
        "calibration": calibration_table(data),
    }
