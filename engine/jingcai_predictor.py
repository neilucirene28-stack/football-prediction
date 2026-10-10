"""竞彩项目专用入口：固定竞彩流程与版本范围，不接受北单调用。"""
from datetime import datetime

from .predictor import PredictError, predict as _core_predict

PROJECT_SCOPE = "jingcai"


def predict(payload: dict, config: dict | None = None,
            asof: datetime | None = None, model: str = PROJECT_SCOPE) -> dict:
    """所有竞彩接口/脚本调用本入口；asof仅供明确标记的离线重放。"""
    if model != PROJECT_SCOPE:
        raise PredictError("这是竞彩专用入口；北单请使用北单独立项目")
    if not isinstance(payload, dict):
        raise PredictError("payload 必须为对象")
    for key in ("model", "project", "project_scope"):
        if key in payload and payload[key] != PROJECT_SCOPE:
            raise PredictError(f"竞彩输入的{key}不得指向其他项目")
    if any(str(key).startswith("beidan") for key in payload):
        raise PredictError("竞彩输入不得携带北单专用字段")
    if config is not None and not isinstance(config, dict):
        raise PredictError("config 必须为对象")
    cfg = dict(config or {})
    for key in ("model", "project", "project_scope"):
        if key in cfg and cfg[key] != PROJECT_SCOPE:
            raise PredictError(f"竞彩配置的{key}不得指向其他项目")
    if any(str(key).startswith("beidan") for key in cfg):
        raise PredictError("竞彩配置不得携带北单专用参数")
    # 项目标识纳入基础版本和校准版本哈希，不能与旧双模式入口混记。
    cfg["project_scope"] = PROJECT_SCOPE
    result = _core_predict(payload, cfg, asof=asof, model=PROJECT_SCOPE)
    result.pop("beidan", None)
    result["project_scope"] = PROJECT_SCOPE
    return result


__all__ = ["predict", "PredictError", "PROJECT_SCOPE"]
