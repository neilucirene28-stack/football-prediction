"""预测接口：POST /api/predict，body 为 engine.predict 的 payload。"""
from typing import Any

from fastapi import APIRouter, HTTPException

from engine.predictor import predict, PredictError
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
    conn = get_conn()
    if conn is not None and result.get("status") == "ok":
        try:
            save_prediction(conn, payload.get("match_id"),
                            payload=payload, result=result)
        except Exception:
            pass
        finally:
            conn.close()
    return result
