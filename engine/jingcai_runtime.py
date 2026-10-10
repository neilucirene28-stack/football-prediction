"""One production entry for Jingcai API and batch predictions.

Retrospective calibration stays in research scripts. Production does not silently
load a historical parameter file, especially one with no authenticated fit data.
"""
import math
import os
from .predictor import PredictError, predict

POLICY = 'no_automatic_historical_calibration'


def runtime_config(overrides=None):
    defaults = {'rho': ('DIXON_COLES_RHO', -.13),
                'kelly_fraction': ('KELLY_FRACTION', .25),
                'model_edge': ('JINGCAI_MODEL_EDGE', 0.)}
    cfg = {key: os.environ.get(env, value) for key, (env, value) in defaults.items()}
    cfg.update(overrides or {})
    if cfg.get('platt') is not None:
        raise PredictError('历史校准参数只能用于独立研究，竞彩生产入口不自动启用')
    for key in defaults:
        try:
            value = float(cfg[key])
        except (TypeError, ValueError) as exc:
            raise PredictError(f'无效生产参数: {key}') from exc
        if not math.isfinite(value):
            raise PredictError(f'生产参数必须是有限数值: {key}')
        cfg[key] = value
    if not -1 < cfg['rho'] < 1 or not 0 <= cfg['kelly_fraction'] <= 1 or not -.1 <= cfg['model_edge'] <= .1:
        raise PredictError('生产参数超出有效范围')
    cfg.update(platt=None, calibration_policy=POLICY)
    return cfg


def predict_jingcai(payload, config=None):
    result = predict(payload, runtime_config(config), model='jingcai')
    result['calibration_policy'] = {
        'name': POLICY, 'production_calibration_enabled': False,
        'note': '历史研究校准候选未获前瞻验证，API与批处理使用同一策略'}
    return result
