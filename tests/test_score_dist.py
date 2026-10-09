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


def test_constrain_hard_constraints_on_candidates():
    # 候选分布经 constrain 后硬约束成立
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
