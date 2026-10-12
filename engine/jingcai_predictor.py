"""竞彩项目专用入口：调用独立竞彩计算核心。"""
from datetime import datetime
from .jingcai_core import predict as _core_predict
from .errors import PredictError
from .jingcai_scope import PROJECT_SCOPE, validated_config


def predict(payload: dict, config: dict | None = None,
            asof: datetime | None = None, model: str = PROJECT_SCOPE) -> dict:
    """asof仅供明确标记的离线重放；该入口始终固定为竞彩。"""
    if model != PROJECT_SCOPE:
        raise PredictError('这是竞彩专用入口；北单请使用北单独立项目')
    cfg = validated_config(payload, config)
    result = _core_predict(payload, cfg, asof=asof)
    result['project_scope'] = PROJECT_SCOPE
    return result


__all__ = ['predict', 'PredictError', 'PROJECT_SCOPE']
