"""攻防强度估计：时间衰减 + 对手强度修正，估计进球期望 λ。

recent 按时间倒序（最新在前），每项：
{'gf':int,'ga':int,'venue':'H'/'A'/'N',
 'opp_attack':float,  # 对手进攻强度（相对联赛均值，缺省1.0）
 'opp_defense':float}  # 对手防守强度（<1=防守好，失球少）
"""
from typing import Iterable, Mapping

# 联赛级别映射：数字越小级别越高；0 = 杯赛/友谊赛（不参与级别判定）。
# 用于跨级别杯赛（如日联杯 J1 vs J2）的强度先验修正。
# 初值依据：英格兰金字塔实证约每级 0.4-0.6 球差距，取保守端，待回测校准。
LEAGUE_TIERS = {
    # -- 日本 --
    "日职联": 1, "日职乙": 2, "日丙": 3, "日足联": 4,
    "日联杯": 0, "日皇杯": 0, "日本超级杯": 0,
    # -- 英格兰 --
    "英超": 1, "英冠": 2, "英甲": 3, "英乙": 4, "英非联": 5,
    "足总杯": 0, "英联杯": 0, "英锦标赛": 0,
    # -- 西班牙 --
    "西甲": 1, "西乙": 2, "西协甲": 3, "国王杯": 0,
    # -- 德国 --
    "德甲": 1, "德乙": 2, "德丙": 3, "德国杯": 0,
    # -- 意大利 --
    "意甲": 1, "意乙": 2, "意丙": 3, "意大利杯": 0,
    # -- 法国 --
    "法甲": 1, "法乙": 2, "法国杯": 0,
    # -- 其他主流 --
    "荷甲": 1, "荷乙": 2, "葡超": 1, "苏超": 1, "土超": 1,
    "美职联": 1, "巴甲": 1, "阿甲": 1, "K联赛": 1, "韩K联": 1,
    "中超": 1, "中甲": 2, "澳超": 1, "沙特联": 1,
    # -- 北单数据源别名（load_history.py 的联赛中文名） --
    "J1": 1, "日职联": 1,  # 同一联赛
    "美职": 1,  # = 美职联
    "巴西甲": 1, "巴甲": 1,  # 同一联赛
    "巴西乙": 2, "巴乙": 2,
    "巴西丙": 3, "巴丙": 3,
    "阿职联": 1, "阿甲": 1,  # 同一联赛
    "墨联": 1,
    "挪超": 1,
    "爱超": 1,
    "苏冠": 2,  # 苏格兰冠军联赛（第二级）
    "欧国联": 0,  # 国家队赛事，不参与俱乐部级别判定
    # -- 洲际/杯赛 --
    "欧冠": 1, "欧联": 1, "欧协联": 1, "亚冠": 1, "亚冠精英": 1,
    "解放者杯": 1, "世俱杯": 1,
    "球会友谊": 0,
}

# 每级联赛差距的强度折扣（乘法因子，作用于 attack/defense 评级）。
# 0.85 进攻 / 1.18 失球 ≈ 典型 λ 下约 0.3-0.4 球摆动，保守初值，待回测校准。
_TIER_ATTACK_FACTOR = 0.85
_TIER_DEFENSE_FACTOR = 1.18

# 攻防评级向联赛均值（1.0）收缩的先验权重（单位：场）。
# 主客场拆分后有效样本常只有 3-5 场，直接估计方差极大（15 场复盘证实 λ 过度离散、
# 让平概率被系统性压低）。收缩是结构性正则，不是拟合参数；待 walk-forward 后再校准。
# 设为 0 可关闭收缩（回退 v2.3 行为）。
_DEFAULT_SHRINK_PRIOR = 3.0


def _shrink_toward_one(rating: float, eff_n: float, prior_w: float) -> float:
    """按有效样本量把评级向 1.0 收缩：(n*r + w*1)/(n+w)。"""
    if prior_w <= 0 or eff_n <= 0:
        return rating
    return (eff_n * rating + prior_w * 1.0) / (eff_n + prior_w)


def team_tier(recent: Iterable[Mapping]) -> int | None:
    """从近期战绩的赛事众数判定球队联赛级别；纯杯赛/未知赛事返回 None。"""
    from collections import Counter
    tiers = [LEAGUE_TIERS[c] for r in recent
             if (c := r.get("comp")) and LEAGUE_TIERS.get(c, 0) > 0]
    if not tiers:
        return None
    return Counter(tiers).most_common(1)[0][0]


def attack_defense(recent: Iterable[Mapping], league_avg_goals: float,
                   venue: str | None = None, decay: float = 0.90,
                   opponent_adjust: bool = True,
                   shrink_prior: float = _DEFAULT_SHRINK_PRIOR
                   ) -> tuple[float, float, dict]:
    """返回 (attack, defense, 诊断)。权重 decay**i，i=0 为最新一场。

    shrink_prior: 向 1.0 收缩的先验权重（场）。有效样本越少，评级越靠近
    联赛均值，抑制小样本噪声导致的 λ 过度离散。0 = 关闭。
    """
    # P0 Bug1修复：venue缺失/非法值视为中性"N"，参与计算。
    # 之前 r.get("venue","N")=="N" 只匹配显式"N"，无venue记录(r.get→None)
    # 被主客场筛选静默排除→评级退回1.0，造成数据丢弃。
    def _norm_venue(r):
        v = r.get("venue")
        return v if v in ("H", "A", "N") else "N"
    rows = [(i, r) for i, r in enumerate(recent)
            if venue is None or _norm_venue(r) == venue or _norm_venue(r) == "N"]
    if not rows or league_avg_goals <= 0:
        return 1.0, 1.0, {"n": 0, "opp_adjust_coverage": 0.0}
    team_avg = league_avg_goals / 2.0  # 每队场均进球（输入为全场总进球）
    w_sum = gf_sum = ga_sum = 0.0
    opp_covered = 0
    for i, r in rows:
        w = decay ** i
        gf, ga = r["gf"], r["ga"]
        if opponent_adjust:
            # 对弱防守队刷进球要打折，对强防守队进球加成。
            # 注意：opp_attack/opp_defense 缺失时默认 1.0（无修正）；
            # opp_adjust_coverage 记录真实覆盖的场次比例，缺失即诚实标注。
            if "opp_attack" in r and "opp_defense" in r:
                opp_covered += 1
            gf = gf / max(float(r.get("opp_defense", 1.0)), 0.2)
            ga = ga / max(float(r.get("opp_attack", 1.0)), 0.2)
        w_sum += w
        gf_sum += w * gf
        ga_sum += w * ga
    attack = max(0.2, (gf_sum / w_sum) / team_avg)
    defense = max(0.2, (ga_sum / w_sum) / team_avg)
    eff_n = w_sum
    attack = _shrink_toward_one(attack, eff_n, shrink_prior)
    defense = _shrink_toward_one(defense, eff_n, shrink_prior)
    return attack, defense, {"n": len(rows),
                             "effective_n": round(eff_n, 2),
                             "shrink_prior": shrink_prior,
                             "opp_adjust_coverage": (round(opp_covered / len(rows), 3)
                                                     if rows else 0.0)}


def estimate_lambdas(home_recent, away_recent, league_avg_goals: float,
                     home_adv_factor: float = 1.12,
                     injury: Mapping | None = None,
                     decay: float = 0.90,
                     home_tier: int | None = None,
                     away_tier: int | None = None,
                     shrink_prior: float = _DEFAULT_SHRINK_PRIOR
                     ) -> tuple[float, float, dict]:
    """估计主客进球期望 (λ_h, λ_a) 及诊断信息。

    injury: 已确认伤停修正，如 {'home_attack': 0.92}，未经确认不得传入。
    home_tier/away_tier: 联赛级别（数字越小越高）；级别不同时应用跨级修正。
    shrink_prior: 攻防评级向联赛均值收缩的先验权重（场），见 attack_defense。
    """
    ah, dh, dh_diag = attack_defense(home_recent, league_avg_goals, venue="H",
                                     decay=decay, shrink_prior=shrink_prior)
    aa, da, da_diag = attack_defense(away_recent, league_avg_goals, venue="A",
                                     decay=decay, shrink_prior=shrink_prior)
    notes_tier = None
    if home_tier is not None and away_tier is not None:
        gap = home_tier - away_tier  # 正数 = 主队联赛级别更低（弱方）
        if gap != 0:
            # 弱方进攻打折、失球上浮；强方反之。乘法因子作用于评级。
            ah *= _TIER_ATTACK_FACTOR ** gap
            dh *= _TIER_DEFENSE_FACTOR ** gap
            aa *= _TIER_ATTACK_FACTOR ** (-gap)
            da *= _TIER_DEFENSE_FACTOR ** (-gap)
            notes_tier = {"home_tier": home_tier, "away_tier": away_tier,
                          "gap": gap}
    team_avg = league_avg_goals / 2.0
    lam_h = ah * da * team_avg * home_adv_factor
    lam_a = aa * dh * team_avg
    notes = {
        "attack_home": round(ah, 3), "defense_home": round(dh, 3),
        "attack_away": round(aa, 3), "defense_away": round(da, 3),
        # P0 Bug1修复：sample计数必须等于attack_defense实际使用的记录数。
        # 之前用独立的list comprehension重算，与筛选逻辑不一致
        # （无venue记录被计入但未被使用），导致degraded谎报。
        # dh_diag/da_diag["n"] 就是各自venue筛选后实际参与计算的记录数。
        "home_sample": dh_diag.get("n", 0),
        "away_sample": da_diag.get("n", 0),
        "home_effective_n": dh_diag.get("effective_n"),
        "away_effective_n": da_diag.get("effective_n"),
        # 对手强度修正的真实覆盖率：目前采集链路不提供 opp_attack/opp_defense，
        # 恒为 0.0（修正静默关闭）。接 Pi-rating 评分表后可回填，此处先诚实标注。
        "opp_adjust_coverage": min(dh_diag.get("opp_adjust_coverage", 0.0),
                                   da_diag.get("opp_adjust_coverage", 0.0)),
        "decay": decay,
        "shrink_prior": shrink_prior,
        "degraded": False,
    }
    if notes_tier:
        notes["tier_adjust"] = notes_tier
        notes["attack_home"] = round(ah, 3)
        notes["defense_home"] = round(dh, 3)
        notes["attack_away"] = round(aa, 3)
        notes["defense_away"] = round(da, 3)
    if injury:
        for key, factor in injury.items():
            if key == "home_attack":
                lam_h *= factor
            elif key == "away_attack":
                lam_a *= factor
            elif key == "home_defense":
                lam_a *= factor  # 主队防守变差 → 客队进球期望上升
            elif key == "away_defense":
                lam_h *= factor
        notes["injury_applied"] = dict(injury)
    # 样本不足标记降级
    if notes["home_sample"] < 5 or notes["away_sample"] < 5:
        notes["degraded"] = True
    lam_h = min(max(lam_h, 0.15), 4.5)
    lam_a = min(max(lam_a, 0.10), 4.0)
    return lam_h, lam_a, notes
