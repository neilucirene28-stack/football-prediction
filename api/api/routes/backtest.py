"""Jingcai evaluation: earliest prospective record per match and model version."""
from fastapi import APIRouter, HTTPException
from engine.jingcai_evaluation import evaluation_report
from ..db import get_conn

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


@router.get("/summary")
def summary(days: int = 30):
    if not 1 <= days <= 365:raise HTTPException(422,"days must be between 1 and 365")
    conn=get_conn()
    if conn is None:
        return {"status":"no_data","note":"未连接数据库；不能确认可结算的赛前记录"}
    try:
        with conn, conn.cursor() as cur:
            # Include pending earliest records; never substitute a later settled forecast.
            # No row limit: a truncated window could discard the true earliest forecast.
            cur.execute("""SELECT p.prediction_id,p.match_id,p.model_version,p.league,
                                  p.kickoff_at,p.predicted_at,p.p_home,p.p_draw,p.p_away,
                                  p.payload,p.signals,p.derivatives,
                                  s.home_goals,s.away_goals,s.settled_at
                           FROM predictions p
                           LEFT JOIN settlements s ON s.prediction_id=p.prediction_id
                           WHERE p.kickoff_at > now()-(%s || ' days')::interval
                           ORDER BY p.kickoff_at,p.predicted_at,p.prediction_id""",(str(days),))
            rows=cur.fetchall()
    except Exception:
        return {"status":"error","note":"复盘记录读取失败"}
    report=evaluation_report(rows);report['days']=days
    report['source']='earliest_jingcai_predictions_left_join_settlements'
    if len(report['by_version'])==1:
        single=next(iter(report['by_version'].values()))
        report.update({k:single[k] for k in ('brier','logloss','calibration_by_class')})
        report['calibration']=single['calibration_by_class']['home']
    return report
