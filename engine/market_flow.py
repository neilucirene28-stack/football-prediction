"""市场资金流特征（资金流向 MVP · 阶段 1）。

只描述"市场在动"，绝不把成交量当第 4 个概率。
三个特征（全部只用开球前数据，全部可解释）：

  drift            初盘→即时去水概率漂移：百家平均 + sharp 子集各一个
  sharp_divergence sharp 子集（水位最低的 N 家）vs 其余公司的分歧幅度
  volume_weight    流动性代理 → 只调节市场信号在融合中的权重

缺失是合法输出：drift 缺失 → 该场退回纯即时赔率并标注。
去水口径与 predictor 的市场信号一致：shin_probs，失败回退
implied_proportional。

companies 行格式（来自 Titan007 raw.x12_companies）：
  {"name": {"open": [h, d, a], "live": [h, d, a], ...}}
"""
from .market import implied_proportional, overround, shin_probs

# sharp 子集：水位最低的 N 家（先验常数，非拟合）
SHARP_N = 8
# sharp/零售分档加权思想（抄 sports-odds-intelligence-platform）
SHARP_UPWEIGHT = 1.5
RETAIL_DOWNWEIGHT = 0.7

# 流动性分档（先验常数，非拟合；volume 单位 USDC）
_PM_TIERS = [
    (1_000_000, 1.00, "deep"),
    (100_000, 0.85, "high"),
    (10_000, 0.70, "medium"),
    (0, 0.55, "thin"),
]
_COMPANY_TIERS = [
    (30, 0.80, "wide"),
    (15, 0.65, "normal"),
    (5, 0.50, "narrow"),
    (1, 0.35, "thin"),
]
_MISSING_WEIGHT = 0.25


def _valid_odds(o) -> bool:
    return (isinstance(o, (list, tuple)) and len(o) == 3
            and all(isinstance(v, (int, float)) and v > 1 for v in o))


def dewig(odds) -> tuple | None:
    """去水：与 predictor 市场信号同口径。"""
    if not _valid_odds(odds):
        return None
    o = (float(odds[0]), float(odds[1]), float(odds[2]))
    try:
        return shin_probs(o)
    except Exception:
        try:
            return implied_proportional(o)
        except Exception:
            return None


def company_probs(companies: dict) -> dict:
    """每家公司的去水概率。返回 {name: {"open": p|None, "live": p|None,
    "overround": float|None}}。"""
    out = {}
    for name, c in (companies or {}).items():
        if not isinstance(c, dict):
            continue
        entry = {"open": dewig(c.get("open")), "live": dewig(c.get("live")),
                 "overround": None}
        if _valid_odds(c.get("live")):
            try:
                entry["overround"] = overround(
                    (float(c["live"][0]), float(c["live"][1]),
                     float(c["live"][2])))
            except Exception:
                pass
        out[name] = entry
    return out


def avg_probs(prob_list: list) -> tuple | None:
    items = [p for p in prob_list if p is not None]
    if not items:
        return None
    n = len(items)
    return (sum(p[0] for p in items) / n,
            sum(p[1] for p in items) / n,
            sum(p[2] for p in items) / n)


def sharp_names(companies: dict, n: int = SHARP_N) -> list:
    """水位（overround）最低的 n 家为 sharp 子集；水位缺失的排后面。"""
    probs = company_probs(companies)
    ranked = sorted(probs.items(),
                    key=lambda kv: (kv[1]["overround"] is None,
                                    kv[1]["overround"] or 9))
    return [name for name, e in ranked[:n] if e["live"] is not None]


def drift_features(companies: dict) -> dict:
    """初盘→即时漂移。返回 dict，缺失处为 None（合法输出）。"""
    probs = company_probs(companies)
    names = list(probs)
    opens = [probs[k]["open"] for k in names]
    lives = [probs[k]["live"] for k in names]
    avg_open, avg_live = avg_probs(opens), avg_probs(lives)

    def _delta(o, l):
        if o is None or l is None:
            return None
        return {"home": round(l[0] - o[0], 4),
                "draw": round(l[1] - o[1], 4),
                "away": round(l[2] - o[2], 4)}

    sharp = sharp_names(companies)
    s_opens = [probs[k]["open"] for k in sharp]
    s_lives = [probs[k]["live"] for k in sharp]
    n_with_open = sum(1 for p in opens if p is not None)
    coverage = ("full" if names and n_with_open == len(names)
                else "partial" if n_with_open > 0 else "none")
    return {
        "avg": _delta(avg_open, avg_live),
        "sharp": _delta(avg_probs(s_opens), avg_probs(s_lives)),
        "n_companies": len(names),
        "n_with_open": n_with_open,
        "n_sharp": len(sharp),
        "sharp_names": sharp,
        "coverage": coverage,
    }


def sharp_divergence(companies: dict) -> float | None:
    """sharp 子集均值 vs 其余公司均值的分歧：max |Δp|。None 表示算不出。

    与"全市场平均"比会被 sharp 自身稀释，故与剔除 sharp 后的其余公司比。
    """
    probs = company_probs(companies)
    names = list(probs)
    sharp = set(sharp_names(companies))
    rest = [k for k in names if k not in sharp]
    sharp_avg = avg_probs([probs[k]["live"] for k in sharp])
    rest_avg = avg_probs([probs[k]["live"] for k in rest])
    if sharp_avg is None or rest_avg is None:
        return None
    return round(max(abs(sharp_avg[i] - rest_avg[i]) for i in range(3)), 4)


def sharp_weighted_market(companies: dict) -> tuple | None:
    """sharp 1.5× / 零售 0.7× 加权去水概率（分档思想抄
    sports-odds-intelligence-platform）。"""
    probs = company_probs(companies)
    sharp = set(sharp_names(companies))
    num = [0.0, 0.0, 0.0]
    den = 0.0
    for name, e in probs.items():
        if e["live"] is None:
            continue
        w = SHARP_UPWEIGHT if name in sharp else RETAIL_DOWNWEIGHT
        for i in range(3):
            num[i] += w * e["live"][i]
        den += w
    if den <= 0:
        return None
    return (num[0] / den, num[1] / den, num[2] / den)


def liquidity_weight(n_companies=None,
                     pm_volume_usdc=None) -> dict:
    """流动性 → 市场信号权重调节系数 volume_weight ∈ [0,1]。

    有 Polymarket 真成交量用成交量分档；无则用 Titan007 覆盖公司数量；
    都没有 → 0.25 + source=missing。只返回观察到的分档，绝不回填编造。
    """
    vol = None
    try:
        vol = float(pm_volume_usdc) if pm_volume_usdc is not None else None
    except (TypeError, ValueError):
        vol = None
    if vol is not None and vol > 0:
        for bound, w, tier in _PM_TIERS:
            if vol >= bound:
                return {"volume_weight": w, "tier": tier,
                        "source": "polymarket",
                        "pm_volume_usdc": round(vol, 2)}
    try:
        n = int(n_companies) if n_companies is not None else 0
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        for bound, w, tier in _COMPANY_TIERS:
            if n >= bound:
                return {"volume_weight": w, "tier": tier,
                        "source": "company_count", "n_companies": n}
    return {"volume_weight": _MISSING_WEIGHT, "tier": "unknown",
            "source": "missing"}


def apply_volume_weight(weights: dict, volume_weight: float,
                        floor: float = 0.5) -> dict:
    """只缩放 market 权重，其余重归一。

    w_market' = w_market · (floor + (1-floor)·v)；v=1 时不动，v 越小
    市场话语权越低（最低保留 floor 比例）。无 market 信号时原样返回。
    """
    w = {k: float(v) for k, v in (weights or {}).items()}
    if w.get("market", 0.0) <= 0:
        return w
    v = max(0.0, min(1.0, float(volume_weight)))
    w["market"] = w["market"] * (floor + (1.0 - floor) * v)
    total = sum(x for x in w.values() if x > 0)
    if total <= 0:
        return w
    return {k: (x / total if x > 0 else 0.0) for k, x in w.items()}


def flow_features(companies: dict, pm_volume_usdc=None) -> dict:
    """一站式：drift + sharp_divergence + volume_weight，缺失安全。"""
    drift = drift_features(companies or {})
    div = sharp_divergence(companies or {})
    liq = liquidity_weight(n_companies=drift["n_companies"],
                           pm_volume_usdc=pm_volume_usdc)
    sharp_mkt = sharp_weighted_market(companies or {})
    return {
        "drift_avg": drift["avg"],          # None = 初盘缺失，该场退回纯即时
        "drift_sharp": drift["sharp"],      # None = 同上
        "drift_coverage": drift["coverage"],
        "n_companies": drift["n_companies"],
        "n_sharp": drift["n_sharp"],
        "sharp_divergence": div,
        "sharp_weighted_market": ([round(p, 4) for p in sharp_mkt]
                                  if sharp_mkt else None),
        "volume_weight": liq["volume_weight"],
        "liquidity_tier": liq["tier"],
        "liquidity_source": liq["source"],
    }


def movement_features(movement: dict, companies: dict | None = None) -> dict:
    """指数走势特征（由 Titan007 x12_movement 驱动，纯函数）。

    输入 movement: {公司名: {"n": N, "points": [{"t","h","d","a"}...时间正序]}}。
    companies 可选：{公司名: {"open":[h,d,a], "live":[h,d,a]}}，用于验证
    快照 drift（初盘->即时）与走势隐含 drift（首点->末点）方向一致性。

    输出全部缺失安全；时间戳只有"MM-DD HH:MM"，只用顺序不用绝对时间。
    """
    out = {"n_companies": 0, "median_n_points": 0,
           "drift_direction_agreement": None, "n_compared": 0,
           "late_steam_avg": None, "max_excursion_avg": None}
    if not movement:
        return out
    names = list(movement)
    out["n_companies"] = len(names)
    ns = sorted(len(movement[n].get("points", [])) for n in names)
    out["median_n_points"] = ns[len(ns) // 2] if ns else 0

    def _home_p(o):
        p = dewig(o)
        return p[0] if p else None

    late_moves, excursions, agree, compared = [], [], 0, 0
    for n in names:
        pts = movement[n].get("points", [])
        probs = [_home_p([p["home"], p["draw"], p["away"]]) for p in pts]
        probs = [p for p in probs if p is not None]
        if len(probs) < 2:
            continue
        # 走势隐含 drift：首点 -> 末点主胜概率变化
        trend_d = probs[-1] - probs[0]
        excursions.append(max(abs(p - probs[0]) for p in probs))
        # 尾部 25% 的平均变动幅度（late steam 探测输入）
        k = max(2, len(probs) // 4)
        tail = probs[-k:]
        late_moves.append(sum(abs(tail[i] - tail[i - 1])
                              for i in range(1, len(tail)))
                          / max(1, len(tail) - 1))
        # 与快照 drift 方向一致性验证
        if companies and n in companies:
            co = companies[n]
            po = _home_p(co["open"]) if co.get("open") else None
            pl = _home_p(co["live"]) if co.get("live") else None
            if po is not None and pl is not None:
                snap_d = pl - po
                if abs(trend_d) > 1e-9 and abs(snap_d) > 1e-9:
                    compared += 1
                    if (trend_d > 0) == (snap_d > 0):
                        agree += 1
    if excursions:
        out["max_excursion_avg"] = round(sum(excursions) / len(excursions), 4)
    if late_moves:
        out["late_steam_avg"] = round(sum(late_moves) / len(late_moves), 4)
    if compared:
        out["n_compared"] = compared
        out["drift_direction_agreement"] = round(agree / compared, 3)
    return out
