"""预测引擎：Elo → 攻防强度 → Dixon-Coles → 市场融合 → Kelly。

纯函数、无 DB 依赖，可独立测试。所有概率均为赛前估计，
调用方必须保证输入快照早于开球时间（predictor.py 会校验）。
"""
from .elo import expected_home_win, update_ratings, win_probability_from_elo
from .strengths import attack_defense, estimate_lambdas
from .poisson import (
    score_matrix, match_probs, btts_prob, over_under_prob,
    asian_handicap_probs, top_scores, total_goals_distribution,
    expected_total_goals, main_goal_interval, half_time_probs,
    half_full_1x2, total_goals_exact, ipf_to_marginals,
)
from .market import (implied_proportional, shin_probs, kelly_fraction,
                     market_drift, fair_handicap, handicap_movement)
from .fusion import (completeness_score, fuse_probs, model_weight_for_grade,
                     ensemble, ensemble_weights, agreement)
from .backtest import (brier_score, log_loss, ranked_probability_score,
                       platt_fit, platt_apply)
from .montecarlo import simulate, maybe_simulate
# Legacy facade exports load only when explicitly requested. Importing the
# Jingcai entry must not import the dual predictor or another project's modules.
_LAZY_EXPORTS = {
    "predict": ("predictor", "predict"),
    "apply_beidan_calibration": ("beidan_calibration", "apply_beidan_calibration"),
    "get_beidan_params": ("beidan_calibration", "get_beidan_params"),
    "beidan_calibration_info": ("beidan_calibration", "beidan_calibration_info"),
    "beidan_upset_risk": ("beidan_upset", "upset_risk"),
    "beidan_risk_tier": ("beidan_upset", "risk_tier"),
    "beidan_should_exclude": ("beidan_upset", "should_exclude"),
    "goal_weight": ("beidan_adjusted_goals", "goal_weight"),
    "adjust_goals": ("beidan_adjusted_goals", "adjust_goals"),
    "adjust_match": ("beidan_adjusted_goals", "adjust_match"),
}


def __getattr__(name):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    module_name, attribute = _LAZY_EXPORTS[name]
    value = getattr(import_module("." + module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY_EXPORTS))

__all__ = [
    "expected_home_win", "update_ratings", "win_probability_from_elo",
    "attack_defense", "estimate_lambdas",
    "score_matrix", "match_probs", "btts_prob", "over_under_prob",
    "asian_handicap_probs", "top_scores", "total_goals_distribution",
    "expected_total_goals", "main_goal_interval", "half_time_probs",
    "half_full_1x2", "total_goals_exact", "ipf_to_marginals",
    "implied_proportional", "shin_probs", "kelly_fraction", "market_drift",
    "fair_handicap", "handicap_movement",
    "completeness_score", "fuse_probs", "model_weight_for_grade",
    "ensemble", "ensemble_weights", "agreement",
    "brier_score", "log_loss", "ranked_probability_score",
    "platt_fit", "platt_apply",
    "simulate", "maybe_simulate",
    "predict",
    "apply_beidan_calibration", "get_beidan_params", "beidan_calibration_info",
    "beidan_upset_risk", "beidan_risk_tier", "beidan_should_exclude",
    "goal_weight", "adjust_goals", "adjust_match",
]
