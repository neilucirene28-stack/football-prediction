"""竞彩入口和计算核心共用的范围校验；不加载任何预测模型。"""

from .errors import PredictError

PROJECT_SCOPE = "jingcai"


def validated_config(payload, config):
    if not isinstance(payload, dict):
        raise PredictError("payload 必须为对象")
    if config is not None and not isinstance(config, dict):
        raise PredictError("config 必须为对象")
    for obj, label in ((payload, "输入"), (config or {}, "配置")):
        for key in ("model", "project", "project_scope"):
            if key in obj and obj[key] != PROJECT_SCOPE:
                raise PredictError(f"竞彩{label}的{key}不得指向其他项目")
        if any(str(key).startswith("beidan") for key in obj):
            raise PredictError(f"竞彩{label}不得携带北单专用字段或参数")
    cfg = dict(config or {})
    cfg["project_scope"] = PROJECT_SCOPE
    return cfg
