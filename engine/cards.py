"""红黄牌预测（MVP）。

黄牌：球队吃牌倾向 × 造牌能力 × 主客场 × 联赛基线，小样本向联赛均值收缩
（复用 v2.4 shrink_prior 思想）；总牌数用独立 Poisson（主客相关性小，MVP 忽略）。
裁判：strictness = 裁判场均牌 / 联赛场均牌，生涯 <5 场不用，5→20 场 confidence 爬坡，
multiplier = 1 + (strictness-1) × confidence × 0.25。无裁判数据时乘子=1.0 并标记。
红牌：稀有事件（场均约 0.06–0.13），单独建模：P(≥1红) = 1 - exp(-λ_red_total × 裁判红牌乘子)。
Booking points：黄=10、红=25（聚合数据近似，忽略单人 35 封顶）。

无数据（未知联赛/球队）→ {"status": "insufficient_data"}，绝不编造。
"""
import json
import math
import os

_HISTORY = None
SHRINK_PRIOR = 3.0      # 收缩先验场数（同 v2.4）
REF_WEIGHT = 0.25       # 裁判乘子权重
REF_MIN_MATCHES = 5     # 裁判最少场次
LAMBDA_CAP = 9.0        # 总牌数期望上限


def _history():
    global _HISTORY
    if _HISTORY is None:
        p = os.path.join(os.path.dirname(__file__), "cards_history.json")
        with open(p, encoding="utf-8") as fh:
            _HISTORY = json.load(fh)
    return _HISTORY


def _shrunk_rate(count: float, n: int, league_avg: float,
                 k: float = SHRINK_PRIOR) -> float:
    return (count + league_avg * k) / (n + k)


def _team_strength(team: dict, side: str, league: dict):
    """返回 (吃牌比, 造牌比, 红牌吃牌比)：>1 表示高于联赛均值。"""
    t = team.get(side, {})
    n = t.get("mp", 0)
    if n == 0:
        return None
    if side == "home":
        base_y, base_r = league["home_yellow"], league["home_red"]
    else:
        base_y, base_r = league["away_yellow"], league["away_red"]
    eat = _shrunk_rate(t.get("yf", 0), n, base_y) / base_y
    make = _shrunk_rate(t.get("ya", 0), n, base_y) / base_y
    red = _shrunk_rate(t.get("rf", 0), n, base_r) / base_r if base_r else 1.0
    return eat, make, red


def _referee_effect(league: dict, referee: str | None):
    """返回 (总牌乘子, 红牌乘子, 裁判信息)。无数据时乘子=1.0。"""
    info = {"name": referee, "used": False, "matches": 0,
            "multiplier": 1.0, "red_multiplier": 1.0}
    if not referee:
        info["reason"] = "无裁判任命数据"
        return 1.0, 1.0, info
    r = league.get("referees", {}).get(referee)
    if not r or r["mp"] < REF_MIN_MATCHES:
        info["reason"] = "裁判样本不足(<5场)或无统计"
        return 1.0, 1.0, info
    lg_total = league["home_yellow"] + league["away_yellow"]
    lg_red = league["home_red"] + league["away_red"]
    conf = min(max((r["mp"] - REF_MIN_MATCHES) / 15.0, 0.0), 1.0)
    strict = (r["cards"] / r["mp"]) / lg_total if lg_total else 1.0
    mult = 1.0 + (strict - 1.0) * conf * REF_WEIGHT
    red_strict = (r["reds"] / r["mp"]) / lg_red if lg_red else 1.0
    red_mult = 1.0 + (red_strict - 1.0) * conf * REF_WEIGHT
    info.update({"used": True, "matches": r["mp"],
                 "avg_cards": round(r["cards"] / r["mp"], 2),
                 "multiplier": round(mult, 3),
                 "red_multiplier": round(red_mult, 3)})
    return mult, red_mult, info


def _pois_over(lmbda: float, line: float) -> float:
    k = int(math.floor(line))
    cdf = sum(math.exp(-lmbda) * lmbda ** i / math.factorial(i)
              for i in range(k + 1))
    return round(1.0 - cdf, 4)


def predict_cards(home_team: str, away_team: str, league_code: str,
                  referee: str | None = None,
                  derby_mult: float = 1.0) -> dict:
    """预测红黄牌。league_code 如 'E0'（英超）。derby_mult 德比/关键战上调，默认 1.0。"""
    hist = _history()
    league = hist["leagues"].get(league_code)
    if league is None:
        return {"status": "insufficient_data",
                "reason": f"未知联赛 {league_code}，无牌数历史"}
    teams = league["teams"]
    if home_team not in teams or away_team not in teams:
        missing = [t for t in (home_team, away_team) if t not in teams]
        return {"status": "insufficient_data",
                "reason": f"球队无牌数历史: {missing}（联赛 {league['name']}）"}
    hs = _team_strength(teams[home_team], "home", league)
    aws = _team_strength(teams[away_team], "away", league)

    lam_h = league["home_yellow"] * hs[0] * aws[1]
    lam_a = league["away_yellow"] * aws[0] * hs[1]
    lam_rh = league["home_red"] * hs[2]
    lam_ra = league["away_red"] * aws[2]

    ref_mult, ref_red_mult, ref_info = _referee_effect(league, referee)
    lam_h *= ref_mult * derby_mult
    lam_a *= ref_mult * derby_mult
    lam_total = min(lam_h + lam_a, LAMBDA_CAP)
    lam_red = (lam_rh + lam_ra) * ref_red_mult

    p_red = 1.0 - math.exp(-lam_red)
    p_home_red = 1.0 - math.exp(-lam_rh * ref_red_mult)
    p_away_red = 1.0 - math.exp(-lam_ra * ref_red_mult)
    booking = 10.0 * lam_total + 25.0 * lam_red

    n_h = teams[home_team]["home"]["mp"]
    n_a = teams[away_team]["away"]["mp"]
    confidence = ("low" if (n_h < 5 or n_a < 5 or not ref_info["used"])
                  else "medium" if (n_h < 20 or n_a < 20) else "high")
    warnings = []
    if not ref_info["used"]:
        warnings.append("裁判数据缺失/不足：总牌数用联赛均值口径，为最大不确定来源")
    if n_h < 5 or n_a < 5:
        warnings.append("球队样本<5场：已向联赛均值收缩")

    return {
        "status": "ok",
        "league": league["name"],
        "exp_home_yellow": round(lam_h, 2),
        "exp_away_yellow": round(lam_a, 2),
        "exp_total_yellow": round(lam_total, 2),
        "exp_booking_points": round(booking, 1),
        "p_over_3_5": _pois_over(lam_total, 3.5),
        "p_over_4_5": _pois_over(lam_total, 4.5),
        "p_red": round(p_red, 4),
        "p_home_red": round(p_home_red, 4),
        "p_away_red": round(p_away_red, 4),
        "referee": ref_info,
        "confidence": confidence,
        "warnings": warnings,
    }
