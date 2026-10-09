"""候选比分分布（GPT比分分布替换 P1）。

实验A：共享比赛节奏的双Poisson混合（首选）
  Q_A(i,j; δ) = 0.5·Pois(i;(1-δ)λH)·Pois(j;(1-δ)λA)
              + 0.5·Pois(i;(1+δ)λH)·Pois(j;(1+δ)λA)
  0 ≤ δ ≤ 0.35；两状态平均进球率仍为原λ。

实验B：主客独立负二项（第二候选）
  G_s ~ NB(μ_s, k)，Var = μ_s + μ_s²/k
  PMF: C(g+k-1,g)·(k/(k+μ))^k·(μ/(k+μ))^g
  k→∞ 时退化为 Poisson。

两种分布生成原始矩阵 Q 后，都经 Dixon-Coles 低比分修正，
再走 score_constrain.constrain_matrix() 锁定1X2与均值。

纯 Python 实现（环境 scipy/numpy 不兼容），用 math.lgamma 算 NB。
"""
import math

from .poisson import pmf as poisson_pmf
from .score_constrain import adaptive_max_goals


def _dc_correct(m, lam_h, lam_a, rho):
    """Dixon-Coles 低比分四格修正（就地）。"""
    if rho == 0.0:
        return m
    n = len(m)
    if n > 0:
        m[0][0] *= max(1 - lam_h * lam_a * rho, 0.0)
    if n > 1:
        m[0][1] *= max(1 + lam_h * rho, 0.0)
        m[1][0] *= max(1 + lam_a * rho, 0.0)
        m[1][1] *= max(1 - rho, 0.0)
    total = sum(sum(row) for row in m)
    if total > 0:
        m = [[v / total for v in row] for row in m]
    return m


def mixture_matrix(lam_h, lam_a, delta, rho=-0.13):
    """实验A：双Poisson混合原始矩阵。返回 (Q, max_goals)。

    共享比赛节奏的两状态（GPT审计修复）：
      Q(i,j) = 0.5·Pois(i;(1-δ)λH)·Pois(j;(1-δ)λA)
             + 0.5·Pois(i;(1+δ)λH)·Pois(j;(1+δ)λA)
    注意：不是边际混合再乘积（那会消掉正相关，变成4个交叉状态）。
    """
    d = min(max(float(delta), 0.0), 0.35)
    slow_h, slow_a = lam_h * (1 - d), lam_a * (1 - d)
    fast_h, fast_a = lam_h * (1 + d), lam_a * (1 + d)
    mg, _ = adaptive_max_goals(
        lambda k: poisson_pmf(k, slow_h) * 0.5 + poisson_pmf(k, fast_h) * 0.5,
        lambda k: poisson_pmf(k, slow_a) * 0.5 + poisson_pmf(k, fast_a) * 0.5,
    )
    n = mg + 1
    # 预计算各状态的单队 pmf
    ph_slow = [poisson_pmf(i, slow_h) for i in range(n)]
    pa_slow = [poisson_pmf(j, slow_a) for j in range(n)]
    ph_fast = [poisson_pmf(i, fast_h) for i in range(n)]
    pa_fast = [poisson_pmf(j, fast_a) for j in range(n)]
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            # 共享两状态：慢状态联合 + 快状态联合
            m[i][j] = (0.5 * ph_slow[i] * pa_slow[j]
                       + 0.5 * ph_fast[i] * pa_fast[j])
    m = _dc_correct(m, lam_h, lam_a, rho)
    return m, mg


def _nb_pmf(g, mu, k):
    """负二项 PMF（均值μ，离散k）。"""
    if mu <= 0:
        return 1.0 if g == 0 else 0.0
    if k <= 0:
        raise ValueError("k must be positive")
    # log P = lgamma(g+k)-lgamma(k)-lgamma(g+1) + k*log(k/(k+mu)) + g*log(mu/(k+mu))
    lp = (math.lgamma(g + k) - math.lgamma(k) - math.lgamma(g + 1)
          + k * math.log(k / (k + mu)) + g * math.log(mu / (k + mu)))
    return math.exp(lp)


def negbin_matrix(mu_h, mu_a, k, rho=-0.13):
    """实验B：独立负二项原始矩阵。返回 (Q, max_goals)。

    k 越大越接近 Poisson；k 需 > 0。向 Poisson 收缩即取大 k。
    """
    k = max(float(k), 0.5)
    mg, _ = adaptive_max_goals(
        lambda kk: _nb_pmf(kk, mu_h, k),
        lambda kk: _nb_pmf(kk, mu_a, k),
    )
    n = mg + 1
    m = [[0.0] * n for _ in range(n)]
    ph = [_nb_pmf(i, mu_h, k) for i in range(n)]
    pa = [_nb_pmf(j, mu_a, k) for j in range(n)]
    for i in range(n):
        for j in range(n):
            m[i][j] = ph[i] * pa[j]
    m = _dc_correct(m, mu_h, mu_a, rho)
    return m, mg


def poisson_baseline_matrix(lam_h, lam_a, rho=-0.13):
    """v2.10基线：独立Poisson + DC（自适应网格，保证可比性）。"""
    mg, _ = adaptive_max_goals(
        lambda k: poisson_pmf(k, lam_h),
        lambda k: poisson_pmf(k, lam_a),
    )
    n = mg + 1
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            m[i][j] = poisson_pmf(i, lam_h) * poisson_pmf(j, lam_a)
    m = _dc_correct(m, lam_h, lam_a, rho)
    return m, mg
