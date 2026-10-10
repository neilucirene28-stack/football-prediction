"""预测接口：POST /api/predict，body 为 engine.predict 的 payload。"""
from typing import Any

from fastapi import APIRouter, HTTPException

from engine.jingcai_predictor import predict, PredictError
from .. import config
from ..db import get_conn
from ..persist import save_prediction

router = APIRouter(prefix="/api/predict", tags=["predict"])


@router.post("")
def run_predict(payload: dict[str, Any]):
    try:
        result = predict(payload, config.ENGINE_CONFIG)
    except PredictError as e:
        raise HTTPException(422, str(e))
    # 尽力记录（有 DB 时）；自学习回路：不可变 + 幂等
    if result.get("status") != "ok":
        result["persistence"] = {"status": "not_saved", "reason": "prediction_not_ok"}
        return result
    conn = get_conn()
    persistence = {"status": "not_saved", "reason": "database_unavailable"}
    if conn is not None and result.get("status") == "ok":
        try:
            prediction_id = save_prediction(conn, payload.get("match_id"),
                                            payload=payload, result=result)
            persistence = {"status": "saved", "prediction_id": prediction_id}
        except Exception as exc:
            persistence = {"status": "not_saved", "reason": "write_failed",
                           "error_type": type(exc).__name__}
        finally:
            conn.close()
    result["persistence"] = persistence
    return result
