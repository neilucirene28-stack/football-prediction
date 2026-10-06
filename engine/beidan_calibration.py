"""北单专属 Platt 校准（engine/beidan_calibration.py）

与竞彩 engine/calibration.json 物理隔离。
训练数据：北单26101期55场（2026-09-30预测概率 vs 实际赛果）。
按玩法分别校准，因为北单六玩法的概率分布与竞彩不同。

用法：
    from engine.beidan_calibration import apply_beidan_calibration
    calibrated = apply_beidan_calibration('sp', 0.55)  # 胜平负玩法，原始概率0.55
"""

import json
import math
import os

_CALIB = None
_CALIB_PATH = os.path.join(os.path.dirname(__file__), "beidan_calibration.json")


def _load():
    global _CALIB
    if _CALIB is None:
        try:
            with open(_CALIB_PATH, encoding="utf-8") as fh:
                _CALIB = json.load(fh)
        except OSError:
            _CALIB = {"params": {}}
    return _CALIB


def get_beidan_params(playtype: str) -> tuple[float, float] | None:
    """获取某玩法的 (a, b) 参数。playtype: sp|rq|goals|hf|dxds"""
    params = _load().get("params", {}).get(playtype)
    if not params:
        return None
    return params["a"], params["b"]


def apply_beidan_calibration(playtype: str, p: float) -> float:
    """对北单某玩法的原始概率应用专属 Platt 校准。

    无参数时原样返回（不编造）。
    """
    ab = get_beidan_params(playtype)
    if ab is None:
        return p
    a, b = ab
    p = min(max(p, 1e-6), 1 - 1e-6)
    x = math.log(p / (1 - p))
    return 1.0 / (1.0 + math.exp(-(a * x + b)))


def beidan_calibration_info() -> dict:
    """返回校准元信息（版本、训练数据说明）。"""
    d = _load()
    return {
        "version": d.get("version"),
        "trained_on": d.get("trained_on"),
        "playtypes": sorted(d.get("params", {}).keys()),
    }
