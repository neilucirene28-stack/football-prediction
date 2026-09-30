"""Elo 评分：长期球队实力轨道。"""
import math


def expected_home_win(ra: float, rb: float, home_adv: float = 65.0) -> float:
    """主队（先手 ra 为主队评分）的期望得分。"""
    return 1.0 / (1.0 + 10.0 ** (-(ra - rb + home_adv) / 400.0))


def update_ratings(ra: float, rb: float, outcome: str,
                   k: float = 30.0, home_adv: float = 65.0) -> tuple[float, float]:
    """赛后更新。outcome: 'H' 主胜 / 'D' 平 / 'A' 客胜。返回 (新ra, 新rb)。"""
    if outcome not in ("H", "D", "A"):
        raise ValueError("outcome must be H/D/A")
    score_a = {"H": 1.0, "D": 0.5, "A": 0.0}[outcome]
    exp_a = expected_home_win(ra, rb, home_adv)
    new_a = ra + k * (score_a - exp_a)
    # 零和：客队期望 = 1 - 主队期望
    new_b = rb + k * ((1.0 - score_a) - (1.0 - exp_a))
    return new_a, new_b


def win_probability_from_elo(ra: float, rb: float,
                             home_adv: float = 65.0) -> tuple[float, float, float]:
    """仅从 Elo 粗估 1X2（平局取经验 0.25，剩余按期望分配）。粗糙，仅作参考。"""
    exp_h = expected_home_win(ra, rb, home_adv)
    p_draw = 0.25
    rest = 1.0 - p_draw
    p_home = rest * exp_h
    p_away = rest * (1.0 - exp_h)
    return p_home, p_draw, p_away
