"""融合层：完整度评分 + 多信号动态加权集成。

信号：
  model  - Poisson/Dixon-Coles（含时间衰减、对手修正、伤停折入 λ）
  elo    - Elo 轨道独立信号（可选）
  market - 市场去水概率（可选）

缺失信号的权重按 V4.1 第十节规则重新归一化分配。
"""


def completeness_score(present: dict) -> tuple[int, str]:
    """present: 各模块是否可用。返回 (分数, 等级 S/A/B/C/D)。"""
    weights = {
        "form": 25,        # 基础战绩
        "venue_split": 15,  # 主客场拆分
        "odds": 15,        # 欧赔
        "asian": 15,       # 亚盘
        "ou": 10,          # 大小球
        "lineup": 10,      # 阵容/伤停
        "advanced": 10,    # xG/排名/交易所等高级数据
    }
    score = sum(w for k, w in weights.items() if present.get(k))
    if score >= 85:
        grade = "S"
    elif score >= 70:
        grade = "A"
    elif score >= 55:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"
    return score, grade


def model_weight_for_grade(grade: str, edge: float = 0.0) -> float:
    """模型主信号的基础权重；edge 为复盘给出的模型相对市场优势。"""
    base = {"S": 0.55, "A": 0.50, "B": 0.40, "C": 0.30, "D": 0.0}[grade]
    w = base + max(min(edge, 0.10), -0.10)
    return min(max(w, 0.0), 0.65)


def ensemble_weights(grade: str, has_market: bool, has_elo: bool,
                     edge: float = 0.0) -> dict[str, float]:
    """动态权重：模型主信号按完整度定锚，剩余按 7:3 分给市场/Elo。

    缺失的信号权重为 0，剩余重新归一（V4.1 第十节）。
    """
    w_model = model_weight_for_grade(grade, edge)
    rest = 1.0 - w_model
    w_market = rest * 0.7 if has_market else 0.0
    w_elo = rest * 0.3 if has_elo else 0.0
    # 缺失信号的权重按比例分给剩余信号
    missing = rest - w_market - w_elo
    alive = w_model + w_market + w_elo
    if alive <= 0:
        return {"model": 0.0, "market": 0.0, "elo": 0.0}
    if missing > 0 and alive > 0:
        w_model += missing * (w_model / alive)
        w_market += missing * (w_market / alive)
        w_elo += missing * (w_elo / alive)
    total = w_model + w_market + w_elo
    return {"model": w_model / total, "market": w_market / total,
            "elo": w_elo / total}


def ensemble(signals: dict[str, tuple[float, float, float] | None],
             weights: dict[str, float]) -> tuple[float, float, float]:
    """加权融合后严格归一到 100%。None 信号被跳过（权重应已为 0）。"""
    fused = [0.0, 0.0, 0.0]
    for name, probs in signals.items():
        if probs is None:
            continue
        w = weights.get(name, 0.0)
        for i in range(3):
            fused[i] += w * probs[i]
    total = sum(fused)
    if total <= 0:
        raise ValueError("ensemble: no live signal")
    return tuple(v / total for v in fused)


def fuse_probs(p_model: tuple[float, float, float],
               p_market: tuple[float, float, float],
               w_model: float) -> tuple[float, float, float]:
    """兼容旧接口：模型 vs 市场两信号融合。"""
    return ensemble({"model": p_model, "market": p_market, "elo": None},
                    {"model": w_model, "market": 1 - w_model, "elo": 0.0})


def agreement(signals: list[tuple[float, float, float]]) -> float:
    """模型一致度 0-1：各信号首选方向越一致越接近 1。"""
    if len(signals) < 2:
        return 1.0
    favs = [max(range(3), key=lambda i: s[i]) for s in signals]
    return sum(1 for f in favs if f == favs[0]) / len(favs)
