"""V4.1 新增能力的测试：时间衰减、对手修正、总进球、半场、合理盘口、
多信号融合、Platt 校准、Monte Carlo。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.strengths import attack_defense
from engine.poisson import (score_matrix, total_goals_distribution,
                            expected_total_goals, main_goal_interval,
                            half_time_probs)
from engine.market import fair_handicap, handicap_movement
from engine.fusion import ensemble, ensemble_weights
from engine.backtest import platt_fit, platt_apply
from engine.montecarlo import simulate, maybe_simulate


def _recent(pattern):
    # pattern: list[(gf,ga)]，最新在前
    return [{"gf": gf, "ga": ga, "venue": "H"} for gf, ga in pattern]


def test_time_decay_weights_recent_more():
    # 近期火热（最新3场场均3球）vs 远期火热（最早3场场均3球）
    recent_hot = _recent([(3, 0)] * 3 + [(0, 2)] * 5)
    old_hot = _recent([(0, 2)] * 5 + [(3, 0)] * 3)
    a1, _, _ = attack_defense(recent_hot, 2.7, venue="H")
    a2, _, _ = attack_defense(old_hot, 2.7, venue="H")
    assert a1 > a2  # 时间衰减：近期状态权重更高


def test_opponent_adjustment():
    # 同样进2球，对弱防守队要打折
    vs_weak = [{"gf": 2, "ga": 0, "venue": "H", "opp_defense": 1.6}] * 6
    vs_strong = [{"gf": 2, "ga": 0, "venue": "H", "opp_defense": 0.6}] * 6
    a_weak, _, _ = attack_defense(vs_weak, 2.7, venue="H")
    a_strong, _, _ = attack_defense(vs_strong, 2.7, venue="H")
    assert a_strong > a_weak


def test_total_goals_distribution():
    m = score_matrix(1.6, 1.0)
    d = total_goals_distribution(m)
    assert abs(sum(d.values()) - 1.0) < 1e-9
    assert abs(expected_total_goals(m) - 2.6) < 0.05
    label, p = main_goal_interval(m)
    assert "球" in label and 0 < p < 1


def test_half_time_model():
    ht = half_time_probs(1.8, 1.0)
    assert abs(ht["p_home"] + ht["p_draw"] + ht["p_away"] - 1.0) < 1e-3
    assert ht["p_draw"] > 0.3  # 半场平局概率通常高于全场


def test_fair_handicap_symmetric():
    fair = fair_handicap(score_matrix(1.4, 1.4))
    assert abs(fair["handicap"]) <= 0.25  # 均势比赛合理盘口接近平手
    assert abs(fair["win"] + 0.5 * fair["push"] - 0.5) < 0.06


def test_handicap_movement_resonance():
    mv = handicap_movement({"handicap": -0.25, "home_water": 1.0},
                           {"handicap": -0.5, "home_water": 0.9},
                           model_home_prob=0.55)
    assert mv["direction"] == "升盘" and mv["signal"] == "共振"
    mv2 = handicap_movement({"handicap": -0.5, "home_water": 0.9},
                            {"handicap": -0.25, "home_water": 1.0},
                            model_home_prob=0.55)
    assert mv2["signal"] == "背离"


def test_ensemble_renormalizes_missing():
    w = ensemble_weights("B", has_market=True, has_elo=False)
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["elo"] == 0.0
    # B级模型锚0.40，缺失的Elo权重按比例分给model/market
    assert abs(w["model"] - 0.4878) < 0.001
    p = ensemble({"model": (0.5, 0.3, 0.2), "market": (0.4, 0.3, 0.3),
                  "elo": None}, w)
    assert abs(sum(p) - 1.0) < 1e-9


def test_platt_calibration():
    # 构造系统性过度自信的数据：预测0.8实际只有0.5
    probs = [0.8] * 50 + [0.2] * 50
    labels = [1] * 25 + [0] * 25 + [1] * 10 + [0] * 40
    a, b = platt_fit(probs, labels)
    calibrated = platt_apply(0.8, a, b)
    assert calibrated < 0.8  # 过度自信被往下拉
    assert 0 < calibrated < 1


def test_monte_carlo_matches_analytic():
    mc = simulate(1.5, 1.0, n=20000, seed=42)
    assert abs(mc["p_home"] - 0.48) < 0.03
    assert abs(mc["p_home"] + mc["p_draw"] + mc["p_away"] - 1.0) < 1e-9


def test_monte_carlo_gate():
    r = maybe_simulate(1.5, 1.0, completeness=40)
    assert r["ran"] is False
    r2 = maybe_simulate(1.5, 1.0, completeness=80, n=1000)
    assert r2["ran"] is True
