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


def ipf_to_marginals(matrix, target) -> list[list[float]]:
    """IPF（iterative proportional fitting / raking）：调整比分矩阵，
    使其 1X2 边际分布等于目标分布。

    主胜格 (i>j) / 平局格 (i==j) / 客胜格 (i<j) 构成矩阵的一个完备划分，
    因此对每个区域做一次乘法缩放即可使边际精确等于目标（单步收敛，
    保留迭代语义以应对数值边界）。区域内相对概率保持不变
    （最小 KL 散度调整）。

    用途（B深修）：predict() 先算 ensemble+Platt 校准得到 p_final，
    再对 matrix 做 IPF，使后续所有衍生项（top_scores、handicap_1x2、
    total_goals 等）都从与 p_final 自洽的矩阵计算，根治
    "胜平负首选与比分首选方向打架"。

    target: (p_home, p_draw, p_away)，内部归一化；请传入未 round 的值。
    返回新矩阵，不修改输入。
    """
    n = len(matrix)
    th, td, ta = (float(target[0]), float(target[1]), float(target[2]))
    s = th + td + ta
    if s <= 0:
        raise ValueError("IPF target marginals sum to non-positive")
    th, td, ta = th / s, td / s, ta / s

    rh = sum(matrix[i][j] for i in range(n) for j in range(n) if i > j)
    rd = sum(matrix[i][j] for i in range(n) for j in range(n) if i == j)
    ra = sum(matrix[i][j] for i in range(n) for j in range(n) if i < j)

    def _factor(t, r, name):
        if r <= 0:
            if t > 1e-12:
                raise ValueError(
                    f"IPF: raw {name} region empty but target={t}")
            return 0.0
        return t / r

    fh = _factor(th, rh, "home")
    fd = _factor(td, rd, "draw")
    fa = _factor(ta, ra, "away")

    new = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            f = fh if i > j else (fd if i == j else fa)
            v = matrix[i][j] * f
            new[i][j] = v if v > 0 else 0.0
    tot = sum(sum(row) for row in new)
    if tot > 0:
        new = [[v / tot for v in row] for row in new]
    return new


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


def _outcome(h: int, a: int) -> str:
    return "胜" if h > a else ("平" if h == a else "负")


def half_full_1x2(lambda_home: float, lambda_away: float,
                  ht_factor: float = 0.44,
                  rho: float = 0.0,
                  ft_matrix=None, strict_ft_grid: bool = False) -> dict:
    """半全场胜平负：9 种组合概率（竞彩官方玩法）。

    推导：半场比分矩阵 M_ht（λ×ht_factor，Dixon-Coles 修正，与 half_time_probs
    同口径）× 下半场独立 Poisson（λ×(1-ht_factor)）→ 半场比分与全场比分的
    联合分布，再按（半场胜平负 × 全场胜平负）聚合为 9 种组合。
    注意半场与全场不是独立的，不能直接相乘边际概率。

    ft_matrix: IPF校准后的全场比分矩阵（P0 Bug4修复新增）。
        传入时，对联合分布做重要性重加权
        w(fi,fj) = P_IPF(fi,fj) / P_J(fi,fj)，
        使聚合的全场边际等于校准后胜平负。
        ft_matrix网格外（>10球）的长尾保留原权重，重加权后重归一；
        长尾质量<1e-5，边际误差可忽略。
        为None时保持原行为（仅测试/兼容用途）。

    strict_ft_grid: 竞彩启用；网格外权重为0，严格采用目标矩阵的支持集。
        半场模板网格同时扩至目标矩阵范围，保证目标每格均可重加权。
        默认False保留北单和旧调用的行为。

    返回 {"胜胜": p, "胜平": p, "胜负": p,
           "平胜": p, "平平": p, "平负": p,
           "负胜": p, "负平": p, "负负": p}，加总 = 1。
    """
    if strict_ft_grid and ft_matrix is None:
        raise ValueError("strict_ft_grid requires ft_matrix")
    ht_max = max(10, len(ft_matrix) - 1) if strict_ft_grid else 10
    m_ht = score_matrix(lambda_home * ht_factor, lambda_away * ht_factor,
                        rho=rho, max_goals=ht_max)
    lam_h2 = lambda_home * (1.0 - ht_factor)
    lam_a2 = lambda_away * (1.0 - ht_factor)
    # 下半场进球截断：归一化后尾部可忽略
    max_2h = 15
    pmf_h2 = [pmf(k, lam_h2) for k in range(max_2h + 1)]
    pmf_a2 = [pmf(k, lam_a2) for k in range(max_2h + 1)]
    s_h, s_a = sum(pmf_h2), sum(pmf_a2)
    pmf_h2 = [v / s_h for v in pmf_h2]
    pmf_a2 = [v / s_a for v in pmf_a2]
    n_ht = len(m_ht)
    n_ft = len(ft_matrix) if ft_matrix is not None else 0

    # Pass 1: 联合分布隐含的全场比分分布 P_J(fi,fj)
    ft_j = {}
    for i in range(n_ht):
        for j in range(n_ht):
            p_ht = m_ht[i][j]
            if p_ht == 0.0:
                continue
            for dh in range(max_2h + 1):
                ph2 = pmf_h2[dh]
                if ph2 == 0.0:
                    continue
                fi = i + dh
                for da in range(max_2h + 1):
                    pa2 = pmf_a2[da]
                    p = p_ht * ph2 * pa2
                    if p == 0.0:
                        continue
                    key = (fi, j + da)
                    ft_j[key] = ft_j.get(key, 0.0) + p

    def _w(fi, fj):
        # P0 Bug4: IPF重加权；网格外长尾权重=1
        if ft_matrix is None:
            return 1.0
        if fi >= n_ft or fj >= n_ft:
            return 0.0 if strict_ft_grid else 1.0
        pj = ft_j.get((fi, fj), 0.0)
        if pj <= 0:
            return 0.0
        return ft_matrix[fi][fj] / pj

    # Pass 2: 加权重聚为 9 种组合
    keys = ("胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负")
    combos = {k: 0.0 for k in keys}
    for i in range(n_ht):
        for j in range(n_ht):
            p_ht = m_ht[i][j]
            if p_ht == 0.0:
                continue
            ht_o = _outcome(i, j)
            for dh in range(max_2h + 1):
                ph2 = pmf_h2[dh]
                if ph2 == 0.0:
                    continue
                fi = i + dh
                for da in range(max_2h + 1):
                    pa2 = pmf_a2[da]
                    p = p_ht * ph2 * pa2
                    if p == 0.0:
                        continue
                    fj = j + da
                    combos[ht_o + _outcome(fi, fj)] += p * _w(fi, fj)
    total = sum(combos.values())
    if total > 0:
        combos = {k: v / total for k, v in combos.items()}
    return combos


def total_goals_exact(matrix, tail: int = 7) -> dict:
    """总进球数精确分布（竞彩官方玩法）：{0:p, 1:p, ..., (tail-1):p, "7+":p}。

    tail=7 时返回 0~6 精确球数 + "7+" 归尾，加总 = 1。
    """
    dist = {k: 0.0 for k in range(tail)}
    tail_key = f"{tail}+"
    dist[tail_key] = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            t = i + j
            if t >= tail:
                dist[tail_key] += p
            else:
                dist[t] += p
    return dist
