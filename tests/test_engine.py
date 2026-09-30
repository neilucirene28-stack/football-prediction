import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.poisson import score_matrix, match_probs, btts_prob, over_under_prob, top_scores
from engine.elo import expected_home_win, update_ratings
from engine.market import implied_proportional, shin_probs, kelly_fraction, overround
from engine.fusion import completeness_score, fuse_probs, model_weight_for_grade
from engine.strengths import estimate_lambdas, team_tier


def _mk_games(comp, n=6, gf=2, ga=1):
    return [{"gf": gf, "ga": ga, "venue": "H" if i % 2 == 0 else "A",
             "comp": comp, "date": f"2026-09-{20-i:02d}"} for i in range(n)]


def test_team_tier_mode():
    rec = _mk_games("日职乙", 5) + _mk_games("日联杯", 2)
    assert team_tier(rec) == 2
    assert team_tier(_mk_games("日联杯", 3)) is None  # 纯杯赛
    assert team_tier(_mk_games("未知联赛", 3)) is None


def test_tier_adjust_favors_higher_league():
    # 同样战绩：J2 主队 vs J1 客队，修正后客队 λ 应显著高于主队
    h = _mk_games("日职乙", 6, gf=2, ga=1)
    a = _mk_games("日职联", 6, gf=2, ga=1)
    lam_h0, lam_a0, _ = estimate_lambdas(h, a, 2.70)
    lam_h1, lam_a1, notes = estimate_lambdas(
        h, a, 2.70, home_tier=2, away_tier=1)
    assert notes.get("tier_adjust", {}).get("gap") == 1
    assert lam_h1 < lam_h0  # 弱方进球期望下调
    assert lam_a1 > lam_a0  # 强方进球期望上调
    assert lam_a1 > lam_h1  # 反转：同等战绩下高级别客队占优


def test_tier_adjust_same_tier_noop():
    h = _mk_games("日职联", 6)
    a = _mk_games("日职联", 6)
    lam_h0, lam_a0, _ = estimate_lambdas(h, a, 2.70)
    lam_h1, lam_a1, notes = estimate_lambdas(
        h, a, 2.70, home_tier=1, away_tier=1)
    assert "tier_adjust" not in notes
    assert lam_h1 == lam_h0 and lam_a1 == lam_a0


def test_tier_adjust_none_tier_noop():
    h = _mk_games("日职乙", 6)
    a = _mk_games("日职联", 6)
    lam_h0, lam_a0, _ = estimate_lambdas(h, a, 2.70)
    lam_h1, lam_a1, _ = estimate_lambdas(h, a, 2.70)  # 不传 tier
    assert lam_h1 == lam_h0 and lam_a1 == lam_a0


def test_matrix_normalizes():
    m = score_matrix(1.5, 1.2, rho=-0.13)
    assert abs(sum(sum(r) for r in m) - 1.0) < 1e-9


def test_rho_zero_equals_independent():
    m0 = score_matrix(1.5, 1.2, rho=0.0)
    m1 = score_matrix(1.5, 1.2, rho=-0.13)
    # Dixon-Coles 只直接修正 4 个低比分格（重归一化会带来全局微小缩放）
    assert m0[0][0] != m1[0][0]
    rel_low = abs(m1[0][0] - m0[0][0]) / m0[0][0]
    rel_high = abs(m1[5][4] - m0[5][4]) / m0[5][4]
    assert rel_low > rel_high * 10


def test_match_probs_sum_to_one():
    m = score_matrix(2.0, 0.8)
    assert abs(sum(match_probs(m)) - 1.0) < 1e-9
    ph, pd, pa = match_probs(m)
    assert ph > pa  # 主队更强


def test_btts_and_ou_bounds():
    m = score_matrix(1.5, 1.5)
    assert 0.0 < btts_prob(m) < 1.0
    over, under = over_under_prob(m, 2.5)
    assert abs(over + under - 1.0) < 1e-9
    assert 0.3 < over < 0.7


def test_top_scores_descending():
    top = top_scores(score_matrix(1.4, 1.1), 3)
    assert len(top) == 3
    assert top[0][1] >= top[1][1] >= top[2][1]


def test_elo_zero_sum_and_favorite():
    ra, rb = update_ratings(1600, 1600, "H")
    assert abs((ra + rb) - 3200) < 1e-9
    assert ra > 1600 > rb
    assert expected_home_win(1700, 1500) > 0.5


def test_implied_and_shin_normalize():
    odds = (2.0, 3.4, 3.6)
    assert abs(sum(implied_proportional(odds)) - 1.0) < 1e-9
    assert abs(sum(shin_probs(odds)) - 1.0) < 1e-6
    assert overround(odds) > 0


def test_kelly_value_direction():
    assert kelly_fraction(0.5, 2.5) > 0      # 有价值
    assert kelly_fraction(0.3, 2.5) == 0.0   # 无价值


def test_completeness_grades():
    full = {k: True for k in ("form", "venue_split", "odds", "asian", "ou", "lineup", "advanced")}
    s, g = completeness_score(full)
    assert (s, g) == (100, "S")
    s2, g2 = completeness_score({"form": True})
    assert g2 == "D"
    assert model_weight_for_grade("D") == 0.0


def test_fusion_normalizes():
    f = fuse_probs((0.5, 0.3, 0.2), (0.4, 0.3, 0.3), 0.5)
    assert abs(sum(f) - 1.0) < 1e-9
