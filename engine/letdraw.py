"""让平单独建模（v2.5）：经验贝叶斯先验 + 校准混合 + 规则护栏。

背景（docs/model-v25-changelog.md 有完整数据）：
- 418 场历史回填（2026-09-01~30）显示 Dixon-Coles 比分矩阵系统性低估 P(让平)：
    |rq|=1: 实际 25.0% vs 模型均值 18.5%（缺口 +6.5pp，n=364）
    |rq|=2: 实际 20.9% vs 模型均值 11.8%（缺口 +9.1pp，n=43）
- walk-forward（训练 09-01~20，测试 09-21~30，n_test=87）：
    让球 Brier：w=0.0 → 0.8404；w=0.5 → 0.8215；w=1.0 → 0.8050
  混合权重 0.5 为默认（在修正系统性偏差与保留场次特异性之间取平衡；
  w=1.0 虽更优但会丢掉全部场次特异信号，过于激进）。

用法：
    ph, pd, pa = handicap_1x2(matrix, rq)   # 模型原始输出
    ph2, pd2, pa2 = calibrate_handicap_1x2(ph, pd, pa, rq, league, strength=0.5)
    guard = letdraw_guard(ph2, pd2, pa2, rq)  # 规则护栏（标注/降档建议）
"""

# 分 |让球| 档的全局经验先验（418 场回填，2026-09-01~30）。
# |rq|>=3 样本过小（n=11），与 |rq|=2 合并档位时用 0.21。
TIER_PRIOR = {1: 0.25, 2: 0.21, 3: 0.21}

# 分联赛经验贝叶斯收缩值（|rq|=1，先验均值 0.25 / 先验强度 20 场）。
# 由 364 场 |rq|=1 回填数据计算：shrunk = (d + 20*0.25) / (n + 20)。
# 未知联赛或 n=0 时回退到 TIER_PRIOR[1]。
LEAGUE_PRIOR_1 = {
    "欧国联": 0.291,
    "欧冠": 0.344,
    "巴西甲": 0.310,
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
    "英联杯": 0.200,
}


def _tier(rq: int) -> int:
    a = abs(int(rq))
    return 1 if a <= 1 else (2 if a == 2 else 3)


def letdraw_prior(rq: int, league: str | None = None) -> float:
    """返回让平经验先验概率。

    |rq|=1 且联赛在 LEAGUE_PRIOR_1 中时用分联赛收缩值，否则用分档全局值。
    """
    t = _tier(rq)
    if t == 1 and league and league in LEAGUE_PRIOR_1:
        return LEAGUE_PRIOR_1[league]
    return TIER_PRIOR[t]


def calibrate_handicap_1x2(p_h: float, p_d: float, p_a: float,
                            rq: int, league: str | None = None,
                            strength: float = 0.5) -> tuple[float, float, float]:
    """把模型让球 1X2 概率向经验先验混合，修正系统性低估。

    strength=0 时关闭（返回原值）；strength=1 时完全采用先验。
    混合后 H/A 按原比例重归一，保证三项和为 1。
    """
    if strength <= 0:
        return p_h, p_d, p_a
    strength = min(max(strength, 0.0), 1.0)
    prior = letdraw_prior(rq, league)
    pd2 = (1.0 - strength) * p_d + strength * prior
    rest = 1.0 - pd2
    s = p_h + p_a
    if s > 0:
        ph2 = rest * p_h / s
        pa2 = rest * p_a / s
    else:
        ph2 = pa2 = rest / 2.0
    return ph2, pd2, pa2


def letdraw_guard(p_h: float, p_d: float, p_a: float,
                  rq: int) -> dict:
    """规则版护栏（短期可直接用的标注逻辑），返回标注与降档建议。

    规则（由 418 场回填数据定）：
    - |让球|=1 且 P(让平) > 0.20 → 强制标注"防让平"
    - 让球结论首选与次选概率差 < 0.15 → 建议降档（结论置信下调一档）
    """
    probs = {"让胜": p_h, "让平": p_d, "让负": p_a}
    order = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top, second = order[0], order[1]
    gap = top[1] - second[1]
    flags = []
    if abs(int(rq)) == 1 and p_d > 0.20:
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
