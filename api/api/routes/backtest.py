"""竞彩复盘接口：只读实际预测账本与结算，按版本与同样本分组。"""
from datetime import datetime, timezone
from fastapi import APIRouter
from fastapi import HTTPException
from engine.jingcai_review import build_review, load_review_rows
from ..db import get_conn

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("/summary")
def summary(days: int = 30, version: str | None = None):
    if not 1 <= days <= 3650:
        raise HTTPException(422, "days必须在1～3650之间")
    conn = get_conn()
    if conn is None:
        return {"status": "no_data",
                "note": "未连接数据库。复盘需要已完赛的比赛与赛前预测快照。"}
    asof = datetime.now(timezone.utc)
    try:
        rows = load_review_rows(conn, days=days, asof=asof, limit=2000, version=version)
        report = build_review(rows, asof=asof)
        report.update({"days": days, "query_limit": 2000,
                       "version_filter": version, "coverage": "latest_up_to_query_limit"})
        return report
    except Exception as exc:
        return {"status": "error", "note": "竞彩账本复盘读取失败",
                "error_type": type(exc).__name__}
    finally:
        conn.close()
