"""竞彩独立默认参数；保留v12基线策略，规范基线候选不自动提升为默认。"""

from copy import deepcopy

ENGINE_VERSION = "2.14"
DEFAULT_CONFIG = {
    "rho": -0.13,
    "kelly_fraction": 0.25,
    "model_edge": 0.0,
    "decay": 0.9,
    "ht_factor": 0.44,
    "mc_n": 20000,
    "mc_min_score": 60,
    "platt": None,
    "shrink_prior": 3.0,
    "weak_shrink_prior": 6.0,
    "weak_goal_cap": 2.8,
    "letdraw_strength": 0.5,
    "divergence_gate": 0.15,
    "league_min_matches": 30,
    "strength_baseline_mode": "legacy_v12",
    "market_weight": None,
    "score_shape_mode": "legacy",
    "score_total_market_weight": 0.0,
    "handicap_market_weight": 0.0,
}
WEAK_COMPETITIONS = {"欧国联", "友谊赛", "球会友谊", "亚运男足", "亚运女足"}


def default_config():
    return deepcopy(DEFAULT_CONFIG)
