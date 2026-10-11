"""竞彩比分截断控制。联合遗漏质量，而非单队尾部最大值。"""
from .poisson import pmf

TAIL_TOLERANCE = 1e-6


def score_grid(lambda_home, lambda_away, *, tolerance=TAIL_TOLERANCE,
               start=10, hard_cap=30):
    sh = sum(pmf(k, lambda_home) for k in range(start + 1))
    sa = sum(pmf(k, lambda_away) for k in range(start + 1))
    for upper in range(start, hard_cap + 1):
        tail = max(0.0, 1.0 - sh * sa)
        if tail <= tolerance:
            return upper, tail
        if upper < hard_cap:
            sh += pmf(upper + 1, lambda_home)
            sa += pmf(upper + 1, lambda_away)
    raise ValueError("比分网格达到上限仍不能满足联合尾部误差要求")


def validate_dc(lambda_home, lambda_away, rho):
    factors = (1 - lambda_home * lambda_away * rho,
               1 + lambda_home * rho, 1 + lambda_away * rho, 1 - rho)
    if min(factors) < 0:
        raise ValueError("rho与进球期望组合产生负Dixon-Coles概率；拒绝静默截断")
