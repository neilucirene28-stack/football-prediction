"""竞彩Platt来源约束；旧的无版本／截止证据参数不能自动进入新模型。"""
import json
import re
from pathlib import Path

from .jingcai_inputs import aware_datetime


def validate_provenance(provenance, *, base_version, asof, has_market, has_elo=False):
    if not isinstance(provenance, dict):
        raise ValueError("Platt缺少platt_provenance，不能确认训练版本与时间")
    if provenance.get("model") != "jingcai" or provenance.get("base_model_version") != base_version:
        raise ValueError("Platt基础模型版本不匹配")
    expected_scope = "market_fused" if has_market else ("elo_fused" if has_elo else "form_only")
    if provenance.get("scope") != expected_scope:
        raise ValueError("Platt训练信号范围不匹配；form_only不能套到市场融合")
    training_before = aware_datetime(provenance.get("training_results_available_before"), "Platt.training_results_available_before")
    validation_before = aware_datetime(provenance.get("validation_results_available_before"), "Platt.validation_results_available_before")
    fitted = aware_datetime(provenance.get("fitted_at"), "Platt.fitted_at")
    if not training_before < validation_before <= fitted <= asof:
        raise ValueError("Platt训练、时序验证、拟合时间不合规或晚于预测截止")
    if provenance.get("validation_kind") != "chronological_holdout":
        raise ValueError("Platt需要声明chronological_holdout，不能把随机折交叉验证当作时序验证")
    digest = provenance.get("training_rows_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Platt缺少训练记录SHA256")
    for key in ("n_train", "n_validation"):
        n = provenance.get(key)
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            raise ValueError(f"Platt.{key}必须为正整数")
    return {"status": "declared_provenance_checked_not_independently_authenticated",
            "base_model_version": base_version, "scope": expected_scope,
            "fitted_at": fitted.isoformat(), "training_rows_sha256": digest}


def load_form_only_config(path=None):
    """仅接受有provenance的参数包；旧文件保留，不猜测其训练版本或时间。"""
    path = Path(path) if path is not None else Path(__file__).with_name("calibration.json")
    try:
        artifact = json.loads(path.read_text())
        params = artifact.get("form_only")
        provenance = artifact.get("form_only_provenance")
        if not isinstance(params, dict) or not isinstance(provenance, dict):
            return None, {"status": "disabled", "reason": "missing_version_and_cutoff_provenance"}
        return {"platt": params, "platt_provenance": provenance}, {"status": "candidate_requires_predictor_validation"}
    except (OSError, ValueError, TypeError, AttributeError):
        return None, {"status": "disabled", "reason": "missing_or_invalid_artifact"}
