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
from .predictor import predict
from .beidan_calibration import (apply_beidan_calibration, get_beidan_params,
                                beidan_calibration_info)
from .beidan_upset import (upset_risk as beidan_upset_risk,
                           risk_tier as beidan_risk_tier,
                           should_exclude as beidan_should_exclude)
from .beidan_adjusted_goals import (goal_weight, adjust_goals, adjust_match)

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
