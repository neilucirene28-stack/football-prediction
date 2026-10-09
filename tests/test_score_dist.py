"""P1候选分布单测：双Poisson混合 / 独立负二项。"""
import math
import sys

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from engine.score_dist import mixture_matrix, negbin_matrix, poisson_baseline_matrix, _nb_pmf
from engine.poisson import pmf as poisson_pmf
from engine.score_constrain import constrain_matrix, matrix_means


def _mass(m):
    return sum(sum(row) for row in m)


def test_mixture_delta_zero_equals_poisson():
    m_mix, _ = mixture_matrix(1.8, 1.2, delta=0.0, rho=0.0)
    m_p, _ = poisson_baseline_matrix(1.8, 1.2, rho=0.0)
    assert len(m_mix) == len(m_p)
    diff = max(abs(m_mix[i][j] - m_p[i][j])
               for i in range(len(m_mix)) for j in range(len(m_mix)))
    assert diff < 1e-12, diff


def test_mixture_preserves_mean():
    # 混合前后总均值不变（约束前）
    lam_h, lam_a = 2.0, 1.0
    m, _ = mixture_matrix(lam_h, lam_a, delta=0.3, rho=0.0)
    n = len(m)
    mh = sum(i * m[i][j] for i in range(n) for j in range(n))
    ma = sum(j * m[i][j] for i in range(n) for j in range(n))
    assert abs(mh - lam_h) / lam_h < 0.05, (mh, lam_h)
    assert abs(ma - lam_a) / lam_a < 0.05, (ma, lam_a)


def test_mixture_fatter_tail():
    # δ>0 时尾部更厚：P(总进球≥6) 应大于 Poisson
    lam_h, lam_a = 1.5, 1.5
    m_mix, _ = mixture_matrix(lam_h, lam_a, delta=0.3, rho=0.0)
    m_p, _ = poisson_baseline_matrix(lam_h, lam_a, rho=0.0)
    nm, np_ = len(m_mix), len(m_p)
    t_mix = sum(m_mix[i][j] for i in range(nm) for j in range(nm) if i + j >= 6)
    t_p = sum(m_p[i][j] for i in range(np_) for j in range(np_) if i + j >= 6)
    assert t_mix > t_p, (t_mix, t_p)


def test_nb_large_k_approx_poisson():
    mu = 1.7
    for g in range(8):
        assert abs(_nb_pmf(g, mu, 2000.0) - poisson_pmf(g, mu)) < 1e-3, g


def test_nb_smaller_k_fatter_tail():
    mu = 1.5
    t1 = sum(_nb_pmf(g, mu, 2.0) for g in range(6, 15))
    t2 = sum(_nb_pmf(g, mu, 50.0) for g in range(6, 15))
    assert t1 > t2, (t1, t2)


def test_negbin_matrix_normalized():
    m, _ = negbin_matrix(1.8, 1.1, k=5.0, rho=-0.13)
    assert abs(_mass(m) - 1.0) < 1e-9


def test_constrain_hard_constraints_on_candidates():    # 候选分布经 constrain 后硬约束成立
    lam_h, lam_a, pi = 2.2, 1.4, (0.55, 0.25, 0.20)
    # 先算基线均值
    m0, _ = poisson_baseline_matrix(lam_h, lam_a, rho=-0.13)
    from engine.poisson import ipf_to_marginals
    mh0, ma0 = matrix_means(ipf_to_marginals(m0, pi))
    for builder, kw in [(mixture_matrix, {"delta": 0.25}),
                        (negbin_matrix, {"k": 4.0})]:
        Q, _ = builder(lam_h, lam_a, rho=-0.13, **kw)
        P, info = constrain_matrix(Q, pi, mh0, ma0)
        assert not info["fallback"], (builder.__name__, info)
        assert info["max_1x2_err"] <= 1e-8, info
        assert info["max_mean_err"] <= 1e-6, info


# ---- GPT审计发现：mixture_matrix 必须是共享慢/快两状态 ----
# bug版本是主客独立混合（pi*pj），正相关被消掉。
# 以下测试在bug代码上失败、修复后通过。

def _covariance(m):
    n = len(m)
    mh = sum(i * m[i][j] for i in range(n) for j in range(n))
    ma = sum(j * m[i][j] for i in range(n) for j in range(n))
    e_ha = sum(i * j * m[i][j] for i in range(n) for j in range(n))
    return e_ha - mh * ma


def test_mixture_shared_state_positive_covariance():
    # 共享节奏 → Cov(G_H,G_A) = λH·λA·δ² > 0（rho=0时无DC干扰）
    lam_h, lam_a, delta = 1.8, 1.2, 0.25
    m, _ = mixture_matrix(lam_h, lam_a, delta=delta, rho=0.0)
    cov = _covariance(m)
    expected = lam_h * lam_a * delta ** 2
    assert abs(cov - expected) / expected < 0.05, (cov, expected)


def test_mixture_not_independent_margins():
    # bug版本 Cov≈0；正确版本 Cov 显著为正
    lam_h, lam_a, delta = 2.0, 1.5, 0.3
    m, _ = mixture_matrix(lam_h, lam_a, delta=delta, rho=0.0)
    cov = _covariance(m)
    assert cov > 0.05, f"Cov={cov}，疑似独立边际乘积（bug）"


def test_mixture_specific_cells_match_shared_formula():
    # 特定格子必须等于共享两状态公式，而非边际乘积
    lam_h, lam_a, delta = 1.8, 1.2, 0.25
    m, mg = mixture_matrix(lam_h, lam_a, delta=delta, rho=0.0)
    slow_h, slow_a = lam_h * (1 - delta), lam_a * (1 - delta)
    fast_h, fast_a = lam_h * (1 + delta), lam_a * (1 + delta)
    for (i, j) in [(0, 0), (1, 1), (2, 1), (5, 5)]:
        expected = (0.5 * poisson_pmf(i, slow_h) * poisson_pmf(j, slow_a)
                    + 0.5 * poisson_pmf(i, fast_h) * poisson_pmf(j, fast_a))
        assert abs(m[i][j] - expected) < 1e-12, (i, j, m[i][j], expected)


def test_mixture_joint_slow_fast_states_present():
    # 联合慢/快状态质量：P(慢)+P(快)=1，且交叉状态（慢快/快慢）不存在
    # 检验方式：正确版本下 P(G_H≤1,G_A≤1) 应大于独立边际乘积版本
    lam_h, lam_a, delta = 1.5, 1.5, 0.3
    m, _ = mixture_matrix(lam_h, lam_a, delta=delta, rho=0.0)
    n = len(m)
    p_low = sum(m[i][j] for i in range(2) for j in range(2))
    # 独立边际乘积版本的理论值（bug公式）
    slow_h = lam_h * (1 - delta)
    fast_h = lam_h * (1 + delta)
    pi0 = 0.5 * poisson_pmf(0, slow_h) + 0.5 * poisson_pmf(0, fast_h)
    pi1 = 0.5 * poisson_pmf(1, slow_h) + 0.5 * poisson_pmf(1, fast_h)
    bug_p_low = (pi0 + pi1) ** 2  # 对称时边际相同
    assert abs(p_low - bug_p_low) > 1e-4, \
        f"p_low={p_low} 与独立边际版本 {bug_p_low} 无差异，疑似bug"
