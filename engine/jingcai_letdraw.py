"""竞彩独立让平先验及矩阵重分配；沿用v11数值。历史回填先验尚非独立赛前验证结论。"""

TIER_PRIOR = {1: 0.25, 2: 0.21, 3: 0.21}
LEAGUE_PRIOR_1 = {
    "欧国联": 0.291,
    "欧冠": 0.344,
    "巴西甲": 0.31,
    "解放者杯": 0.286,
    "欧联": 0.267,
    "亚运男足": 0.258,
    "荷乙": 0.259,
    "K联赛": 0.233,
    "友谊赛": 0.233,
    "美职": 0.219,
    "J联赛": 0.229,
    "瑞典超": 0.222,
    "沙特联": 0.207,
    "亚冠": 0.231,
    "J2联赛": 0.185,
    "挪超": 0.185,
    "英联杯": 0.2,
}


def _tier(rq: int) -> int:
    a = abs(int(rq))
    return 1 if a <= 1 else 2 if a == 2 else 3


def letdraw_prior(rq: int, league: str | None = None) -> float:
    """返回让平经验先验概率。

    |rq|=1 且联赛在 LEAGUE_PRIOR_1 中时用分联赛收缩值，否则用分档全局值。
    """
    t = _tier(rq)
    if t == 1 and league and (league in LEAGUE_PRIOR_1):
        return LEAGUE_PRIOR_1[league]
    return TIER_PRIOR[t]


def apply_letdraw_to_matrix(
    matrix: list[list[float]], rq: int, league: str | None = None, strength: float = 0.5
) -> list[list[float]]:
    """在比分矩阵上做让平修正（区域内重分配），返回新矩阵。

    数学（区域内重分配）：
    - rq<0（主让球）：W=主胜区{i>j}，E={(i,j): i−j=−rq}（让平事件）
    - rq>0（主受让）：W=客胜区{i<j}，E={(i,j): i−j=−rq}
    - 目标：P_new(E) = (1−strength)·P_raw(E) + strength·prior（v2.5边际混合语义）
    - 区域内重分配：E内各格乘 c/c0，W\\E内各格乘 (1−c)/(1−c0)，
      其中 c0=P_raw(E)/P(W)，c=P_new(E)/P(W)
    - P(W)严格不变 ⇒ rq<0时 P(让胜)+P(让平) ≡ P(主胜)（误差≤1e-12）

    与 calibrate_handicap_1x2（事后投影）不同，此函数直接修正分布 Q，
    让球概率从修正后矩阵聚合得到，天然满足概率恒等式。

    物理约束：P_new(E) ≤ P(W)（E⊆W），超限时截断。

    strength≤0 或 rq=0 时返回原矩阵的拷贝（不修正）。
    退化保护：c0过小（<1e-12）或过大（>1−1e-12）时跳过修正。
    返回新矩阵，不修改输入。
    """
    n = len(matrix)
    rq = int(rq)
    if rq == 0 or strength <= 0:
        return [row[:] for row in matrix]
    strength = min(max(strength, 0.0), 1.0)
    if rq < 0:

        def in_w(i, j):
            return i > j

    else:

        def in_w(i, j):
            return i < j

    def in_e(i, j):
        return i - j == -rq

    p_w = sum((matrix[i][j] for i in range(n) for j in range(n) if in_w(i, j)))
    p_e = sum(
        (matrix[i][j] for i in range(n) for j in range(n) if in_w(i, j) and in_e(i, j))
    )
    if p_w <= 0:
        return [row[:] for row in matrix]
    c0 = p_e / p_w
    if c0 < 1e-12 or c0 > 1.0 - 1e-12:
        return [row[:] for row in matrix]
    prior = letdraw_prior(rq, league)
    p_e_new = (1.0 - strength) * p_e + strength * prior
    p_e_new = min(p_e_new, p_w)
    c = p_e_new / p_w
    f_e = c / c0
    f_rest = (1.0 - c) / (1.0 - c0)
    new = [row[:] for row in matrix]
    for i in range(n):
        for j in range(n):
            if not in_w(i, j):
                continue
            new[i][j] = matrix[i][j] * (f_e if in_e(i, j) else f_rest)
    return new


def letdraw_guard(p_h: float, p_d: float, p_a: float, rq: int) -> dict:
    """规则版护栏（短期可直接用的标注逻辑），返回标注与降档建议。

    规则（由 418 场回填数据定）：
    - |让球|=1 且 P(让平) > 0.20 → 强制标注"防让平"
    - 让球结论首选与次选概率差 < 0.15 → 建议降档（结论置信下调一档）
    """
    probs = {"让胜": p_h, "让平": p_d, "让负": p_a}
    order = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top, second = (order[0], order[1])
    gap = top[1] - second[1]
    flags = []
    if abs(int(rq)) == 1 and p_d > 0.2:
        flags.append("防让平")
    downgrade = gap < 0.15
    if downgrade:
        flags.append("概率接近建议降档")
    return {
        "top": top[0],
        "top_prob": round(top[1], 4),
        "gap": round(gap, 4),
        "flags": flags,
        "downgrade": downgrade,
    }
