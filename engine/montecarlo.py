"""Monte Carlo 模拟：从 Poisson(λ) 抽样验证解析矩阵。

V4.1 第十五节：只有完整度达标才运行，否则明确显示未运行。
"""
import math
import random


def sample_poisson(lam: float, rng: random.Random) -> int:
    """Knuth 算法（λ 较小时间接高效）；λ 大时用正态近似。"""
    if lam <= 30:
        l = math.exp(-lam)
        k, p = 0, 1.0
        while True:
            k += 1
            p *= rng.random()
            if p <= l:
                return k - 1
    return max(0, int(rng.gauss(lam, math.sqrt(lam)) + 0.5))


def simulate(lambda_home: float, lambda_away: float, n: int = 20000,
             seed: int | None = None) -> dict:
    """返回 1X2、总进球分布、大小球2.5、比分 Top5。"""
    rng = random.Random(seed)
    w = d = 0
    goals: dict[int, int] = {}
    scores: dict[tuple[int, int], int] = {}
    over25 = 0
    for _ in range(n):
        i, j = sample_poisson(lambda_home, rng), sample_poisson(lambda_away, rng)
        if i > j:
            w += 1
        elif i == j:
            d += 1
        t = i + j
        goals[t] = goals.get(t, 0) + 1
        scores[(i, j)] = scores.get((i, j), 0) + 1
        if t >= 3:
            over25 += 1
    top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "n": n,
        "p_home": round(w / n, 4), "p_draw": round(d / n, 4),
        "p_away": round((n - w - d) / n, 4),
        "over25": round(over25 / n, 4),
        "top_scores": [{"score": f"{i}-{j}", "prob": round(c / n, 4)}
                       for (i, j), c in top],
    }


def maybe_simulate(lambda_home: float, lambda_away: float,
                   completeness: int, min_score: int = 60,
                   n: int = 20000, seed: int | None = None) -> dict:
    """完整度门控：不达标返回未运行标记，禁止伪造模拟。"""
    if completeness < min_score:
        return {"ran": False,
                "reason": f"Monte Carlo：未运行（完整度 {completeness} < {min_score}）"}
    out = simulate(lambda_home, lambda_away, n=n, seed=seed)
    out["ran"] = True
    return out
