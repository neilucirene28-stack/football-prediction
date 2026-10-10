"""预测接口：POST /api/predict，body 为 engine.predict 的 payload。"""
from typing import Any

from fastapi import APIRouter, HTTPException

from engine.predictor import PredictError
from engine.jingcai_runtime import predict_jingcai
from engine.jingcai_archive import archive_prediction
from .. import config
from ..db import get_conn
from ..persist import save_prediction

router = APIRouter(prefix="/api/predict", tags=["predict"])


@router.post("")
def run_predict(payload: dict[str, Any]):
    try:
        result = predict_jingcai(payload, config.ENGINE_CONFIG)
    except PredictError as e:
        raise HTTPException(422, str(e))
    if result.get("status") == "ok":
        try:
            result["archive"] = archive_prediction(payload, result)
        except Exception:
            result["archive"] = {"status": "failed", "note": "本地预测留档失败"}
    # 尽力记录（有 DB 时）；自学习回路：不可变 + 幂等
    result["persistence"] = {"status": "unavailable", "note": "预测尚未保存到数据库"}
    conn = get_conn() if result.get("status") == "ok" else None
    if conn is not None and result.get("status") == "ok":
        try:
            prediction_id = save_prediction(conn, payload.get("match_id"),
                            payload=payload, result=result)
            result["persistence"] = {"status": "saved", "prediction_id": prediction_id}
        except Exception:
            conn.rollback()
            result["persistence"] = {"status": "failed", "note": "预测保存失败；不能视为已留档"}
        finally:
            conn.close()
    return result
