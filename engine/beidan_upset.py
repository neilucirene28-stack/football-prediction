"""北单冷门分层（engine/beidan_upset.py）

目标：预测"市场首选翻车"（二分类），不预测赛果本身。
输出每场一个"翻车风险分"（0-1），用于稳胆筛选：高风险场次自动降档或剔除。

设计说明（诚实记录）：
- 理想输入应含"模型概率 vs 市场隐含概率差值、赔率离散度"，但26101期历史
  记录中无市场赔率数据，故当前版本仅用模型侧特征训练。
- 接口保留 market_gap / odds_dispersion 两个可选参数，有市场数据时可传入，
  未来数据齐了可重训。
- 当前为规则+逻辑回归混合：先用26101期55场拟合简单逻辑回归权重。

用法：
    from engine.beidan_upset import upset_risk
    risk = upset_risk(p_model_top=0.55, handicap=0, league='巴西乙')
    # risk in [0,1]，越高越可能翻车
"""

import math

# 逻辑回归权重（26101期55场拟合）
# 特征：[1(截距), 模型top概率, |让球|*0.5, 弱联赛标志]
# 训练标签：1=模型胜平负首选翻车，0=命中
# 重要发现：p_top 系数为正——模型越自信，翻车率越高（过度自信）。
# 这与直觉相反，但是55场数据的实证结果。弱联赛系数为负，
# 可能因模型在弱联赛更保守。样本小（55），权重仅供参考。
_WEIGHTS = {
    "intercept": 1.614,
    "p_top": -2.189,  # 注意：特征用的是 (1-p)，此处为拟合值取反后的等效
    "p_top_neg": 2.189,  # 实际用 (1-p)*2.189，等价于 p*(-2.189)
    "abs_handicap": 0.423,
    "weak_league": -0.356,
}

# 弱联赛集合（低级别、U21、友谊赛等数据稀疏赛事）
WEAK_LEAGUES = frozenset({
    "巴西乙", "英乙", "英甲", "西乙", "苏冠", "爱超", "阿职联",
    "U21", "友谊赛", "芬超", "挪威杯", "罗甲",
})


def _sigmoid(x: float) -> float:
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def upset_risk(p_model_top: float,
               handicap: int = 0,
               league: str = "",
               market_gap: float | None = None,
               odds_dispersion: float | None = None) -> float:
    """计算翻车风险分（0-1）。

    p_model_top: 模型对首选结果的概率（0-1）
    handicap: 让球数（可负）
    league: 联赛名（用于弱联赛标志）
    market_gap: 模型概率 - 市场隐含概率（可选，暂未参与训练）
    odds_dispersion: 赔率离散度（可选，暂未参与训练）

    实证发现（26101期55场）：模型越自信翻车率越高（过度自信），
    故 p 系数为正。
    """
    p = min(max(p_model_top, 0.01), 0.99)
    w = _WEIGHTS
    # 拟合式：z = 1.614 - 2.189*(1-p) + 0.423*|h|*0.5 - 0.356*weak
    logit = (w["intercept"]
             + (-w["p_top_neg"]) * (1 - p)
             + w["abs_handicap"] * abs(handicap) * 0.5
             + w["weak_league"] * (1.0 if league in WEAK_LEAGUES else 0.0))
    # 市场特征预留：有数据时按以下方式接入（权重待重训确定）
    # if market_gap is not None:
    #     logit += w_market_gap * market_gap
    return round(_sigmoid(logit), 4)


def risk_tier(risk: float) -> str:
    """风险分档：低/中/高，用于稳胆筛选。

    阈值基于26101期55场风险分分布（min 0.45, max 0.74, 中位 0.56）：
    - 低：<0.53
    - 中：0.53-0.62
    - 高：>0.62
    """
    if risk < 0.53:
        return "低"
    if risk < 0.62:
        return "中"
    return "高"


def should_exclude(risk: float, threshold: float = 0.65) -> bool:
    """是否应从稳胆中剔除（高风险）。"""
    return risk >= threshold
