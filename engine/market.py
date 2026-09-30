"""市场模块：去水概率、漂移检测、Kelly 价值、合理盘口。"""
import math

from .poisson import asian_handicap_probs


def implied_proportional(odds: tuple[float, float, float]) -> tuple[float, float, float]:
    """等比去水。返回 (p_home, p_draw, p_away)。"""
    inv = [1.0 / o for o in odds]
    total = sum(inv)
    return tuple(v / total for v in inv)


def overround(odds: tuple[float, float, float]) -> float:
    return sum(1.0 / o for o in odds) - 1.0


def shin_probs(odds: tuple[float, float, float],
               tol: float = 1e-10, max_iter: int = 200) -> tuple[float, float, float]:
    """Shin 去水：认为水位主要加在热门身上，迭代求解 z。"""
    q = list(implied_proportional(odds))
    z = 0.05
    for _ in range(max_iter):
        fz = sum((math.sqrt(z * z + 4 * (1 - z) * qi * qi) - z) / (2 * (1 - z))
                 for qi in q) - 1.0
        # 数值导数
        h = 1e-7
        fz2 = sum((math.sqrt((z + h) ** 2 + 4 * (1 - z - h) * qi * qi) - (z + h)) / (2 * (1 - z - h))
                  for qi in q) - 1.0
        deriv = (fz2 - fz) / h
        if abs(deriv) < 1e-12:
            break
        z_new = min(max(z - fz / deriv, 1e-9), 1 - 1e-9)
        if abs(z_new - z) < tol:
            z = z_new
            break
        z = z_new
    probs = [(math.sqrt(z * z + 4 * (1 - z) * qi * qi) - z) / (2 * (1 - z)) for qi in q]
    total = sum(probs)
    return tuple(p / total for p in probs)


def kelly_fraction(p: float, odds: float, fraction: float = 0.25) -> float:
    """四分之一 Kelly（默认）。返回建议仓位比例，≤0 表示无价值。"""
    if odds <= 1.0 or not (0.0 < p < 1.0):
        return 0.0
    b = odds - 1.0
    f = (b * p - (1 - p)) / b * fraction
    return max(f, 0.0)


def market_drift(opening: tuple[float, float, float],
                 live: tuple[float, float, float]) -> dict:
    """初盘→即时去水概率漂移。|Δp|>3% 记为转向信号（定性，不重复计权）。"""
    po = implied_proportional(opening)
    pl = implied_proportional(live)
    names = ("home", "draw", "away")
    deltas = {k: pl[i] - po[i] for i, k in enumerate(names)}
    signals = [k for k, d in deltas.items() if abs(d) > 0.03]
    return {"delta": deltas, "signals": signals}


def fair_handicap(matrix, step: float = 0.25, lo: float = -3.0,
                  hi: float = 3.0) -> dict:
    """模型合理盘口（主队视角，负数=让球）：找上盘期望价值最接近 0.5 的盘口。

    v(h) = P(i+h>j) + 0.5·P(i+h==j)，取 |v-0.5| 最小的 h。
    """
    n = len(matrix)
    best, best_d = 0.0, 1.0
    h = lo
    while h <= hi + 1e-9:
        win = sum(matrix[i][j] for i in range(n) for j in range(n)
                  if i + h > j + 1e-9)
        push = sum(matrix[i][j] for i in range(n) for j in range(n)
                   if abs(i + h - j) < 1e-9)
        v = win + 0.5 * push
        d = abs(v - 0.5)
        if d < best_d:
            best, best_d = h, d
        h = round(h + step, 10)
    win, push, lose = asian_handicap_probs(matrix, best)
    return {"handicap": round(best, 2), "win": round(win, 4),
            "push": round(push, 4), "lose": round(lose, 4)}


def handicap_movement(opening: dict, live: dict,
                      model_home_prob: float) -> dict:
    """盘口变化分析。opening/live: {'handicap': float, 'home_water': float}。

    返回：方向（升盘/降盘/不变）、水位变化、模型市场共振/背离。
    handicap 为主队视角（负数=主让球），数字变小 = 升盘（让更多）。
    """
    ho, hl = opening["handicap"], live["handicap"]
    wo, wl = opening.get("home_water"), live.get("home_water")
    if hl < ho - 1e-9:
        direction = "升盘"
    elif hl > ho + 1e-9:
        direction = "降盘"
    else:
        direction = "不变"
    water_move = None
    if wo is not None and wl is not None:
        if wl < wo - 1e-9:
            water_move = "降水"
        elif wl > wo + 1e-9:
            water_move = "升水"
    model_supports_home = model_home_prob >= 0.45
    resonance = None
    if direction == "升盘" and model_supports_home:
        resonance = "共振"      # 模型看好主队 + 市场升盘
    elif direction == "降盘" and model_supports_home:
        resonance = "背离"      # 模型看好主队但市场退盘 → 风险+
    elif direction == "升盘" and not model_supports_home:
        resonance = "背离"
    return {"direction": direction, "from": ho, "to": hl,
            "water_move": water_move, "signal": resonance}
