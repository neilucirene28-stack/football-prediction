"""比分矩阵：独立 Poisson → Dixon-Coles 低比分修正 → 各类市场聚合。"""
import math


def pmf(k: int, lam: float) -> float:
    return lam ** k * math.exp(-lam) / math.factorial(k)


def score_matrix(lambda_home: float, lambda_away: float,
                 rho: float = 0.0, max_goals: int = 10) -> list[list[float]]:
    """返回 (max_goals+1)² 的比分概率矩阵；rho 为 Dixon-Coles 参数（≤0）。"""
    m = [[0.0] * (max_goals + 1) for _ in range(max_goals + 1)]
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = pmf(i, lambda_home) * pmf(j, lambda_away)
            if rho != 0.0:
                if i == 0 and j == 0:
                    p *= 1 - lambda_home * lambda_away * rho
                elif i == 0 and j == 1:
                    p *= 1 + lambda_home * rho
                elif i == 1 and j == 0:
                    p *= 1 + lambda_away * rho
                elif i == 1 and j == 1:
                    p *= 1 - rho
            m[i][j] = max(p, 0.0)
    total = sum(sum(row) for row in m)
    return [[v / total for v in row] for row in m]


def match_probs(matrix) -> tuple[float, float, float]:
    """由矩阵聚合 1X2。"""
    n = len(matrix)
    ph = sum(matrix[i][j] for i in range(n) for j in range(n) if i > j)
    pd = sum(matrix[i][j] for i in range(n) for j in range(n) if i == j)
    pa = sum(matrix[i][j] for i in range(n) for j in range(n) if i < j)
    return ph, pd, pa


def btts_prob(matrix) -> float:
    n = len(matrix)
    return 1.0 - sum(matrix[i][0] for i in range(n)) - sum(matrix[0][j] for j in range(n)) + matrix[0][0]


def over_under_prob(matrix, line: float) -> tuple[float, float]:
    """返回 (大球概率, 小球概率)。"""
    n = len(matrix)
    over = sum(matrix[i][j] for i in range(n) for j in range(n)
               if i + j > line)
    # 恰好等于整数线时计一半（走水近似）
    push = sum(matrix[i][j] for i in range(n) for j in range(n)
               if float(i + j) == float(line)) * 0.5
    over += push
    return over, 1.0 - over


def asian_handicap_probs(matrix, handicap: float) -> tuple[float, float, float]:
    """handicap 为主队让球（负数=让球）。返回 (赢盘, 走水, 输盘)。"""
    n = len(matrix)
    win = sum(matrix[i][j] for i in range(n) for j in range(n) if i + handicap > j)
    push = sum(matrix[i][j] for i in range(n) for j in range(n) if i + handicap == j)
    return win, push, 1.0 - win - push


def handicap_1x2(matrix, line: int) -> tuple[float, float, float]:
    """竞彩让球胜平负：主队让 line 球后的 1X2。"""
    n = len(matrix)
    ph = sum(matrix[i][j] for i in range(n) for j in range(n) if i + line > j)
    pd = sum(matrix[i][j] for i in range(n) for j in range(n) if i + line == j)
    pa = 1.0 - ph - pd
    return ph, pd, pa


def top_scores(matrix, n: int = 3) -> list[tuple[str, float]]:
    scored = []
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            scored.append((f"{i}-{j}", p))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:n]


def total_goals_distribution(matrix) -> dict:
    """总进球数分布：{0:p, 1:p, 2:p, 3:p, 4:p, '5+':p}。"""
    dist = {k: 0.0 for k in range(5)}
    dist["5+"] = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            t = i + j
            if t >= 5:
                dist["5+"] += p
            else:
                dist[t] += p
    return dist


def expected_total_goals(matrix) -> float:
    n = len(matrix)
    return sum((i + j) * matrix[i][j]
               for i in range(n) for j in range(n))


def main_goal_interval(matrix, width: int = 2) -> tuple[str, float]:
    """主要进球区间：找概率最大的连续 width 个球数，如 '2-3球'。"""
    dist = total_goals_distribution(matrix)
    keys = [0, 1, 2, 3, 4, "5+"]
    probs = [dist[k] for k in keys]
    best, best_p = (0, width - 1), 0.0
    for s in range(len(keys) - width + 1):
        p = sum(probs[s:s + width])
        if p > best_p:
            best, best_p = (s, s + width - 1), p
    label = f"{keys[best[0]]}-{keys[best[1]]}球"
    return label, round(best_p, 4)


def half_time_probs(lambda_home: float, lambda_away: float,
                    ht_factor: float = 0.44,
                    rho: float = 0.0) -> dict:
    """半场模型：λ_HT = λ_FT × FirstHalfFactor，再跑一次比分矩阵。"""
    m = score_matrix(lambda_home * ht_factor, lambda_away * ht_factor, rho=rho)
    ph, pd, pa = match_probs(m)
    n = len(m)
    ht_scores = sorted(
        ((f"{i}-{j}", m[i][j]) for i in range(min(n, 3)) for j in range(min(n, 3))),
        key=lambda x: x[1], reverse=True)[:4]
    return {
        "p_home": round(ph, 4), "p_draw": round(pd, 4), "p_away": round(pa, 4),
        "ht_factor": ht_factor,
        "top_scores": [{"score": s, "prob": round(p, 4)} for s, p in ht_scores],
    }
