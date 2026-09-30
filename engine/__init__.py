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
)
from .market import (implied_proportional, shin_probs, kelly_fraction,
                     market_drift, fair_handicap, handicap_movement)
from .fusion import (completeness_score, fuse_probs, model_weight_for_grade,
                     ensemble, ensemble_weights, agreement)
from .backtest import (brier_score, log_loss, ranked_probability_score,
                       platt_fit, platt_apply)
from .montecarlo import simulate, maybe_simulate
from .predictor import predict

__all__ = [
    "expected_home_win", "update_ratings", "win_probability_from_elo",
    "attack_defense", "estimate_lambdas",
    "score_matrix", "match_probs", "btts_prob", "over_under_prob",
    "asian_handicap_probs", "top_scores", "total_goals_distribution",
    "expected_total_goals", "main_goal_interval", "half_time_probs",
    "implied_proportional", "shin_probs", "kelly_fraction", "market_drift",
    "fair_handicap", "handicap_movement",
    "completeness_score", "fuse_probs", "model_weight_for_grade",
    "ensemble", "ensemble_weights", "agreement",
    "brier_score", "log_loss", "ranked_probability_score",
    "platt_fit", "platt_apply",
    "simulate", "maybe_simulate",
    "predict",
]
