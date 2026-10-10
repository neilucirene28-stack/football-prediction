"""竞彩预测与原输入绑定；封存校验不认可历史重放。"""
from datetime import datetime
import hashlib
import json

from .jingcai_inputs import aware_datetime


def input_digest(payload):
    # 对调用方原输入计算；default=str兼容采集器raw中的日期对象。
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_live_record(payload, result, *, now=None, require_hash=True):
    if result.get("status") != "ok" or result.get("model") != "jingcai":
        raise ValueError("竞彩账本仅接受ok竞彩预测")
    if result.get("project_scope", "jingcai") != "jingcai":
        raise ValueError("竞彩账本不得接受其他项目范围的预测")
    for key in ("model", "project", "project_scope"):
        if key in payload and payload[key] != "jingcai":
            raise ValueError("竞彩账本原输入不得指向其他项目")
    if result.get("evaluation_mode") != "live":
        raise ValueError("历史重放或缺少live标记不能写入真实赛前预测账本")
    if (not isinstance(result.get("model_version"), str) or not result["model_version"].strip()
            or result["model_version"] == "unknown"):
        raise ValueError("竞彩预测缺少模型版本")
    digest = result.get("input_sha256")
    if require_hash and not digest:
        raise ValueError("竞彩预测缺少原输入摘要，不能封存")
    if digest and digest != input_digest(payload):
        raise ValueError("预测与原输入摘要不匹配，不能封存或复盘")
    match = result.get("match") or {}
    for key in ("home", "away", "kickoff_at"):
        if match.get(key) != payload.get(key):
            raise ValueError(f"预测与原输入的{key}不匹配")
    kickoff = aware_datetime(payload.get("kickoff_at"), "kickoff_at")
    snapshot = aware_datetime(payload.get("snapshot_at"), "snapshot_at")
    generated = aware_datetime(result.get("prediction_generated_at"), "prediction_generated_at")
    cutoff = aware_datetime(result.get("feature_cutoff_at"), "feature_cutoff_at")
    evaluated = aware_datetime(result.get("evaluation_asof"), "evaluation_asof")
    now = now if now is not None else datetime.now().astimezone()
    if snapshot != cutoff or snapshot > evaluated or evaluated > generated:
        raise ValueError("竞彩快照、截止或生成时间顺序无效")
    if generated > now or generated >= kickoff or now >= kickoff:
        raise ValueError("竞彩封存时已开球或生成时间在未来，不能记录赛前预测")
    return {"kickoff": kickoff, "generated": generated, "snapshot": snapshot}
