"""constrain_matrix 约束层测试（GPT比分分布P0）。"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.poisson import score_matrix, ipf_to_marginals, match_probs, pmf
from engine.score_constrain import (
    constrain_matrix, matrix_means, adaptive_max_goals,
    TOL_1X2, TOL_MEAN)


def _close(a, b, tol=1e-9):
    return abs(a - b) < tol


def test_constrain_reproduces_ipf():
    """Poisson输入+IPF自身均值 → 精确复现IPF矩阵。"""
    for lh, la, rho, pf in [
        (1.8, 1.2, -0.13, (0.55, 0.26, 0.19)),
        (1.3, 0.2, -0.13, (0.65, 0.20, 0.15)),
        (0.9, 1.6, -0.13, (0.25, 0.27, 0.48)),
    ]:
        Q = score_matrix(lh, la, rho=rho)
        base = ipf_to_marginals(Q, pf)
        mh0, ma0 = matrix_means(base)
        P, info = constrain_matrix(Q, pf, mh0, ma0)
        assert not info["fallback"], f"不应回退: {info}"
        assert info["max_1x2_err"] < TOL_1X2
        assert info["max_mean_err"] < TOL_MEAN
        n = len(Q)
        maxdiff = max(abs(P[i][j] - base[i][j])
                      for i in range(n) for j in range(n))
        assert maxdiff < 1e-12, f"格差过大: {maxdiff}"


def test_constrain_nontrivial_means():
    """目标均值≠IPF均值时，倾斜生效且硬约束成立。"""
    Q = score_matrix(1.8, 1.2, rho=-0.13)
    pf = (0.55, 0.26, 0.19)
    base = ipf_to_marginals(Q, pf)
    mh0, ma0 = matrix_means(base)
    P, info = constrain_matrix(Q, pf, mh0 * 1.1, ma0 * 1.1)
    assert not info["fallback"]
    assert info["converged"]
    mh, ma = matrix_means(P)
    assert _close(mh, mh0 * 1.1, TOL_MEAN)
    assert _close(ma, ma0 * 1.1, TOL_MEAN)
    ph, pd, pa = match_probs(P)
    assert _close(ph, pf[0], TOL_1X2)
    assert _close(pd, pf[1], TOL_1X2)
    assert _close(pa, pf[2], TOL_1X2)
    # 概率合法
    tot = sum(sum(row) for row in P)
    assert _close(tot, 1.0, 1e-9)
    assert all(v >= 0 for row in P for v in row)


def test_constrain_fallback_on_empty_region():
    """某区域无质量但目标>0 → 回退，不抛错。"""
    # 构造只有主胜格有质量的矩阵
    Q = [[0.0] * 3 for _ in range(3)]
    Q[2][0] = 1.0
    P, info = constrain_matrix(Q, (0.5, 0.3, 0.2), 1.5, 0.5)
    assert info["fallback"]
    # 回退结果仍是合法矩阵
    tot = sum(sum(row) for row in P)
    assert tot >= 0


def test_adaptive_grid():
    """自适应网格：截断误差<1e-6。"""
    for lam in (1.5, 3.0, 5.0):
        m, err = adaptive_max_goals(lambda k, l=lam: pmf(k, l),
                                    lambda k, l=lam: pmf(k, l))
        assert err < 1e-6, f"λ={lam} 截断误差 {err}"
        assert m >= 10
    # 高λ需要更大网格
    m1, _ = adaptive_max_goals(lambda k: pmf(k, 1.5), lambda k: pmf(k, 1.5))
    m5, _ = adaptive_max_goals(lambda k: pmf(k, 5.0), lambda k: pmf(k, 5.0))
    assert m5 > m1


def test_gpt_acceptance_case():
    """GPT验收case：λ=(1.3,0.2), ρ=-0.13，等式约束成立。"""
    Q = score_matrix(1.3, 0.2, rho=-0.13)
    pf = (0.651552, 0.20, 0.148448)
    base = ipf_to_marginals(Q, pf)
    mh0, ma0 = matrix_means(base)
    P, info = constrain_matrix(Q, pf, mh0, ma0)
    assert not info["fallback"]
    assert info["max_1x2_err"] < 1e-8
    assert info["max_mean_err"] < 1e-6
