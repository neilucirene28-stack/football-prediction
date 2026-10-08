"""预测门面：输入一场比赛的数据，输出 V4.2/V4.1 格式的完整预测。

融合 V4.1 的关键改进：
- 时间衰减 + 对手强度修正的近期状态
- model/elo/market 三信号动态集成（缺失重归一）
- 一致性检查（十七节）
- 置信度 = 数据质量 × 模型一致 × 市场稳定 × 阵容确定（十八节）
- 半场模型、模型合理盘口、总进球分布、冷门比分
- Monte Carlo（完整度门控，十五节）
"""
from datetime import datetime
import hashlib
import json
import os

from .elo import win_probability_from_elo
from .strengths import estimate_lambdas, team_tier
from .poisson import (score_matrix, match_probs, btts_prob, over_under_prob,
                      asian_handicap_probs, handicap_1x2, top_scores,
                      total_goals_distribution, expected_total_goals,
                      main_goal_interval, half_time_probs,
                      half_full_1x2, total_goals_exact, ipf_to_marginals)
from .market import (implied_proportional, shin_probs, overround,
                     kelly_fraction, market_drift, fair_handicap,
                     handicap_movement)
from .fusion import (completeness_score, ensemble, ensemble_weights,
                     agreement, fuse_probs)
from .backtest import platt_apply
from .montecarlo import maybe_simulate
from .cards import predict_cards
from .market_flow import (flow_features, apply_volume_weight,
                          movement_features)
from .letdraw import (calibrate_handicap_1x2, letdraw_guard)
from .beidan_calibration import apply_beidan_calibration
from .beidan_upset import upset_risk as beidan_upset_risk, risk_tier as beidan_risk_tier

_CARDS_ALIAS = None


def _cards_alias():
    global _CARDS_ALIAS
    if _CARDS_ALIAS is None:
        p = os.path.join(os.path.dirname(__file__), "cards_alias.json")
        try:
            with open(p, encoding="utf-8") as fh:
                _CARDS_ALIAS = json.load(fh)
        except OSError:
            _CARDS_ALIAS = {}
    return _CARDS_ALIAS


def _cards_block(payload):
    """红黄牌预测（MVP）。payload 可选 cards={league_code, referee, derby}。

    中文队名经 cards_alias.json 映射到 football-data 英文名；
    无映射/无历史/未知联赛 → insufficient_data，绝不编造。
    """
    spec = payload.get("cards") or {}
    league_code = spec.get("league_code")
    if not league_code:
        return {"status": "insufficient_data",
                "reason": "未指定联赛代码，无牌数历史"}
    alias = _cards_alias().get(league_code, {})
    home_en = alias.get(payload.get("home", ""))
    away_en = alias.get(payload.get("away", ""))
    if not home_en or not away_en:
        return {"status": "insufficient_data",
                "reason": f"球队无中英映射: "
                          f"{[t for t, e in ((payload.get('home'), home_en), (payload.get('away'), away_en)) if not e]}"}
    return predict_cards(home_en, away_en, league_code,
                         referee=spec.get("referee"),
                         derby_mult=1.1 if spec.get("derby") else 1.0)


class PredictError(ValueError):
    pass


# 引擎大版本：引擎代码逻辑变化时手动递增（参数变化由下方哈希覆盖）。
# v2.7: B深修——比分矩阵 IPF 校准，所有全场衍生项从校准后矩阵计算。
ENGINE_VERSION = "2.8"


# 弱赛事集合（v2.5）：国家队/友谊赛性质赛事，弱队进攻 λ 系统性高估。
# 依据：69 场弱赛事回填，预测期望 3.09 vs 实际 2.84（+0.24 球系统性高估）；
# 其他 502 场偏差仅 -0.03。见 docs/model-v25-changelog.md。
WEAK_COMPETITIONS = {"欧国联", "友谊赛", "球会友谊", "亚运男足", "亚运女足"}


def model_version(cfg: dict) -> str:
    """模型版本号：由全部被跟踪参数计算得出，参数一变版本即变。

    杜绝"3 月和 8 月的预测被混成一个系统"——回测/学习时必须按版本分组。
    """
    canon = json.dumps(cfg, sort_keys=True, default=str)
    digest = hashlib.sha1(canon.encode("utf-8")).hexdigest()[:8]
    return f"v{ENGINE_VERSION}+{digest}"


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise PredictError("时间必须带时区")
    return dt


def _recent_ok(rows) -> bool:
    return isinstance(rows, list) and len(rows) >= 6 and all(
        isinstance(r, dict) and isinstance(r.get("gf"), int)
        and isinstance(r.get("ga"), int) for r in rows)


def _score_winner(score: str) -> int:
    i, j = (int(x) for x in score.split("-"))
    return 0 if i > j else (1 if i == j else 2)


def consistency_check(p_final, top3, asian, ou) -> list[str]:
    """十七节：所有市场必须方向一致，否则报告冲突。"""
    issues = []
    fav = max(range(3), key=lambda i: p_final[i])
    w0 = _score_winner(top3[0]["score"])
    if w0 != fav and p_final[fav] - p_final[w0] > 0.08:
        issues.append(
            f"首选比分 {top3[0]['score']} 的胜负方向与 1X2 首选不一致")
    if asian and fav == 0 and asian["win"] < 0.35:
        issues.append("1X2 看好主胜但亚盘赢盘概率过低，方向存疑")
    if asian and fav == 2 and asian["lose"] < 0.35:
        issues.append("1X2 看好客胜但亚盘输盘概率过低，方向存疑")
    if ou and ou["over"] > 0.6 and top3[0]["prob"] > 0 and \
            sum(int(x) for x in top3[0]["score"].split("-")) <= 1:
        issues.append("大小球倾向大球但首选比分为小比分，进球模型冲突")
    return issues


def _confidence_score(completeness, signals, overround_v, has_lineup,
                      issues, drift_signals: int = 0) -> float:
    """十八节：Confidence = 数据质量 × 模型一致 × 市场稳定 × 阵容确定。"""
    dq = completeness / 100.0
    ag = agreement([s for s in signals if s is not None])
    stability = 1.0 - min(max(overround_v, 0.0), 0.20) / 0.20 * 0.30
    # 盘口大幅漂移说明市场分歧大，稳定性再打折
    stability *= (1.0 - 0.10 * min(drift_signals, 2))
    lineup = 1.0 if has_lineup else 0.60
    conf = 100.0 * dq * ag * stability * lineup
    if issues:
        conf *= 0.90
    return round(max(min(conf, 100.0), 0.0), 1)


def _upset_risk(p_final, top3, movement, p_draw) -> tuple[float, list[str]]:
    """十三节：冷门风险 0-100（多因子），不单纯看赔率。"""
    risk = 100.0 * (1 - max(p_final))
    factors = []
    if p_draw > 0.30:
        risk += 8
        factors.append("平局概率异常高")
    small_conc = sum(s["prob"] for s in top3
                     if sum(int(x) for x in s["score"].split("-")) <= 1)
    if small_conc > 0.30:
        risk += 5
        factors.append("小比分概率集中")
    if movement and movement.get("signal") == "背离":
        risk += 10
        factors.append("模型与盘口背离")
    if max(p_final) < 0.45:
        risk += 5
        factors.append("实力接近、无明显热门")
    return round(max(min(risk, 100.0), 0.0), 1), factors


def predict(payload: dict, config: dict | None = None,
          asof: datetime | None = None, model: str = "jingcai") -> dict:
    """asof: 回测用虚拟"现在"时间。生产调用必须为 None。

    model: "jingcai"（默认，现有行为）| "beidan"（北单适配层：
           北单专属Platt校准 + 冷门分层风险分 + 六玩法输出标记）。
    数学引擎（Poisson/Dixon-Coles/Elo）两者共享，不重写。
    """
    if model not in ("jingcai", "beidan"):
        raise PredictError(f"未知模型: {model}")
    cfg = {"rho": -0.13, "kelly_fraction": 0.25, "model_edge": 0.0,
           "decay": 0.90, "ht_factor": 0.44, "mc_n": 20000,
           "mc_min_score": 60, "platt": None,
           # 攻防评级向联赛均值收缩的先验权重（场）；0 = 关闭收缩
           "shrink_prior": 3.0,
           # 弱赛事（欧国联/友谊赛等）用更强的收缩先验；0 = 不区分
           "weak_shrink_prior": 6.0,
           # 弱赛事期望总进球封顶；0 = 不封顶
           "weak_goal_cap": 2.8,
           # 让平校准混合权重（0=关闭，0.5=默认）；见 engine/letdraw.py
           "letdraw_strength": 0.5,
           # 背离门控：模型信号与市场信号首选方向不一致且差距超过此阈值时
           # 自动触发背离警告并下调信心一档。0 = 关闭门控
           "divergence_gate": 0.15}
    cfg.update(config or {})
    version = model_version(cfg)

    # ---- 1. 输入校验与防泄漏 ----
    try:
        kickoff = _parse_dt(payload["kickoff_at"])
        snapshot = _parse_dt(payload["snapshot_at"])
    except KeyError as e:
        raise PredictError(f"缺少必填时间字段: {e}")
    now = asof or datetime.now().astimezone()
    if kickoff <= now:
        raise PredictError("比赛已开赛或为历史比赛：拒绝赛后预测")
    if snapshot > kickoff:
        raise PredictError("快照晚于开球：存在未来泄漏")
    home, away = payload.get("home"), payload.get("away")
    if not home or not away or home == away:
        raise PredictError("主客队无效")

    home_recent = payload.get("home_recent") or []
    away_recent = payload.get("away_recent") or []
    league_avg = float(payload.get("league_avg_goals", 2.70))

    # ---- 2. 完整度评分 ----
    present = {
        "form": _recent_ok(home_recent) and _recent_ok(away_recent),
        "venue_split": any(r.get("venue") for r in home_recent + away_recent),
        "odds": bool(payload.get("odds")),
        "asian": bool(payload.get("asian")),
        "ou": payload.get("ou_line") is not None,
        "lineup": bool(payload.get("injury")),
        "advanced": False,
    }
    score, grade = completeness_score(present)
    if grade == "D":
        return {"status": "insufficient_data", "grade": grade,
                "completeness": score,
                "reason": "数据完整度不足（D级），拒绝给出概率预测"}

    # ---- 3. 进球期望（时间衰减 + 对手修正 + 联赛级别修正） ----
    # 中立场地（如杯赛决赛/锦标赛）：主队无主场加成
    neutral = bool(payload.get("neutral_site"))
    ht, at_ = team_tier(home_recent), team_tier(away_recent)
    # v2.5：弱赛事用更强的收缩先验（弱队进攻 λ 系统性高估，见 WEAK_COMPETITIONS）
    # 用户显式传入 shrink_prior 时优先尊重用户设置（可复现/可关闭）
    competition = payload.get("competition", "")
    is_weak = competition in WEAK_COMPETITIONS
    user_cfg = config or {}
    if is_weak and "shrink_prior" not in user_cfg and cfg.get("weak_shrink_prior"):
        eff_shrink = cfg["weak_shrink_prior"]
    else:
        eff_shrink = cfg["shrink_prior"]
    lam_h, lam_a, lam_notes = estimate_lambdas(
        home_recent, away_recent, league_avg,
        injury=payload.get("injury"), decay=cfg["decay"],
        home_adv_factor=1.0 if neutral else 1.12,
        home_tier=ht, away_tier=at_,
        shrink_prior=eff_shrink)
    lam_notes["home_adv_factor"] = 1.0 if neutral else 1.12
    lam_notes["neutral_site"] = neutral
    lam_notes["weak_competition"] = is_weak
    if is_weak:
        lam_notes["shrink_prior_effective"] = eff_shrink

    h2h = payload.get("h2h") or []
    if h2h:
        # h2h 以主队视角记录：gf=主队进球。近3场净胜球 → λ 微调（±3%封顶）
        gd = sum(r["gf"] - r["ga"] for r in h2h[-3:])
        adj = max(min(gd * 0.01, 0.03), -0.03)
        lam_h *= (1 + adj)
        lam_a *= (1 - adj)
        lam_notes["h2h_adjust"] = round(adj, 4)

    # v2.5：弱赛事期望总进球封顶（按比例缩放，保持主客比例）
    weak_cap = cfg.get("weak_goal_cap") or 0
    if is_weak and weak_cap > 0 and lam_h + lam_a > weak_cap:
        scale = weak_cap / (lam_h + lam_a)
        lam_h *= scale
        lam_a *= scale
        lam_notes["weak_goal_cap_applied"] = {
            "cap": weak_cap, "scale": round(scale, 4)}

    # ---- 4. 比分矩阵 → 模型信号 ----
    matrix = score_matrix(lam_h, lam_a, rho=cfg["rho"])
    p_model = match_probs(matrix)

    # ---- 5. Elo 独立信号 ----
    p_elo = None
    if payload.get("elo"):
        e = payload["elo"]
        p_elo = win_probability_from_elo(float(e["home"]), float(e["away"]))

    # ---- 6. 市场信号 ----
    p_market = market = None
    odds = payload.get("odds")
    o = None
    if odds:
        o = (float(odds["home"]), float(odds["draw"]), float(odds["away"]))
        try:
            p_market = shin_probs(o)
        except Exception:
            p_market = implied_proportional(o)
        market = {"odds": list(o), "overround": round(overround(o), 4),
                  "implied": [round(p, 4) for p in p_market]}
        if payload.get("opening_odds"):
            oo = payload["opening_odds"]
            market["drift"] = market_drift(
                (float(oo["home"]), float(oo["draw"]), float(oo["away"])), o)

    # ---- 6b. 资金流特征（影子，不进生产融合） ----
    flow = flow_shadow = None
    _companies = (payload.get("raw") or {}).get("x12_companies")
    if _companies and p_market is not None:
        try:
            _pm = payload.get("polymarket") or {}
            flow = flow_features(_companies,
                                 pm_volume_usdc=_pm.get("volume_usdc"))
            _mv = (payload.get("raw") or {}).get("x12_movement")
            if _mv:
                flow["movement"] = movement_features(_mv, _companies)
        except Exception:
            flow = None

    # ---- 7. 三信号动态集成 ----
    weights = ensemble_weights(grade, p_market is not None,
                               p_elo is not None, edge=cfg["model_edge"])
    p_final = ensemble({"model": p_model, "market": p_market, "elo": p_elo},
                       weights)

    # ---- 7b. 资金流影子（生产 p_final 不用 w_flow） ----
    if flow is not None:
        w_flow = apply_volume_weight(weights, flow["volume_weight"])
        _pf = ensemble({"model": p_model, "market": p_market, "elo": p_elo},
                       w_flow)
        flow_shadow = {
            "weights": {k: round(v, 4) for k, v in w_flow.items()},
            "p_home": round(_pf[0], 4), "p_draw": round(_pf[1], 4),
            "p_away": round(_pf[2], 4),
        }

    platt_on = False
    if cfg.get("platt"):
        # 回测拟合的 Platt 参数：{"home": (A,B), "draw": (A,B), "away": (A,B)}
        cal = [platt_apply(p, *cfg["platt"][k])
               for p, k in zip(p_final, ("home", "draw", "away"))]
        s = sum(cal)
        if s > 0:
            p_final = tuple(c / s for c in cal)
            platt_on = True
    p_home, p_draw, p_away = (round(p, 4) for p in p_final)

    # ---- 7c. 比分矩阵 IPF 校准（B深修） ----
    # 使 matrix 的 1X2 边际与校准后 p_final 一致，后续所有全场衍生项
    # （top_scores、handicap_1x2、total_goals、over_under、asian、btts 等）
    # 全部从校准后矩阵计算，根治"胜平负首选与比分首选方向打架"。
    # 注意用未 round 的 p_final，避免 4 位小数截断导致边际失真。
    # 半场子模型（half_time_probs / half_full_1x2）是独立的 HT 口径，
    # 其内部 1X2 与半场比分本就自洽，无校准目标，不做 IPF。
    matrix_cal = ipf_to_marginals(matrix, p_final)

    # ---- 8. 价值检测 ----
    value = []
    if o and market:
        names = ("home", "draw", "away")
        for idx, (name, p, odd) in enumerate(
                zip(names, (p_home, p_draw, p_away), o)):
            edge = round(p - market["implied"][idx], 4)
            kelly = round(kelly_fraction(p, odd, cfg["kelly_fraction"]), 4)
            if edge > 0.05:
                value.append({"outcome": name, "edge": edge, "kelly": kelly})

    # ---- 9. 衍生市场（校准后矩阵；B深修） ----
    # 全场衍生项全部从 matrix_cal 计算，与校准后 1X2 自洽。
    # raw 矩阵的值保留为 *_raw 对比字段，便于回测诊断。
    ou_line = payload.get("ou_line")
    fair = fair_handicap(matrix_cal)
    deriv = {
        "btts": round(btts_prob(matrix_cal), 4),
        "over_under": None,
        "asian": None,
        "handicap_1x2": None,
        "top_scores": [{"score": s, "prob": round(p, 4)}
                       for s, p in top_scores(matrix_cal)],
        "top_scores_raw": [{"score": s, "prob": round(p, 4)}
                           for s, p in top_scores(matrix)],
        "p_1x2_raw": [round(p, 4) for p in p_model],
        "total_goals": {str(k): round(v, 4)
                        for k, v in total_goals_distribution(matrix_cal).items()},
        "expected_goals": round(expected_total_goals(matrix_cal), 2),
        "expected_goals_raw": round(expected_total_goals(matrix), 2),
        "main_goal_interval": None,
        "fair_handicap": fair,
        "cards": _cards_block(payload),
        "half_time": half_time_probs(lam_h, lam_a, ht_factor=cfg["ht_factor"],
                                     rho=cfg["rho"]),
        # 输出格式升级（对齐竞彩官方五大玩法）：半全场 9 种组合 + 总进球精确分布。
        # 纯输出项，不改变任何概率逻辑，ENGINE_VERSION 保持 2.5。
        # P0 Bug4：传入IPF后矩阵，使半全场全场边际与最终胜平负一致
        "half_full_1x2": {k: round(v, 4) for k, v in
                          half_full_1x2(lam_h, lam_a,
                                        ht_factor=cfg["ht_factor"],
                                        rho=cfg["rho"],
                                        ft_matrix=matrix_cal).items()},
        "total_goals_exact": {str(k): round(v, 4) for k, v in
                              total_goals_exact(matrix_cal).items()},
    }
    interval, interval_p = main_goal_interval(matrix_cal)
    deriv["main_goal_interval"] = {"label": interval, "prob": interval_p}
    if ou_line is not None:
        over, under = over_under_prob(matrix_cal, float(ou_line))
        deriv["over_under"] = {"line": ou_line, "over": round(over, 4),
                               "under": round(under, 4)}
    asian = payload.get("asian")
    movement = None
    if asian and asian.get("handicap") is not None:
        win, push, lose = asian_handicap_probs(matrix_cal, float(asian["handicap"]))
        deriv["asian"] = {"handicap": asian["handicap"],
                          "win": round(win, 4), "push": round(push, 4),
                          "lose": round(lose, 4),
                          "fair_handicap": fair["handicap"],
                          "line_bias": round(float(asian["handicap"])
                                             - fair["handicap"], 2)}
        if asian.get("opening_handicap") is not None:
            movement = handicap_movement(
                {"handicap": float(asian["opening_handicap"]),
                 "home_water": asian.get("opening_home_water")},
                {"handicap": float(asian["handicap"]),
                 "home_water": asian.get("home_water")},
                p_home)
            deriv["asian"]["movement"] = movement
    handicap = payload.get("handicap_line")
    if handicap is not None:
        # 让球口径先从校准后矩阵算 raw 值，再走现有让平校准流程（顺序不变）
        h, d, a = handicap_1x2(matrix_cal, int(handicap))
        # v2.5：让平校准（修正矩阵系统性低估 P(让平)；见 engine/letdraw.py）
        # P0 Bug3：传入IPF后全场胜平负作事件包含约束参照
        h2, d2, a2 = calibrate_handicap_1x2(
            h, d, a, int(handicap), league=competition,
            strength=cfg.get("letdraw_strength", 0.5),
            p_home=p_final[0], p_away=p_final[2])
        deriv["handicap_1x2"] = {"line": handicap, "p_home": round(h2, 4),
                                 "p_draw": round(d2, 4), "p_away": round(a2, 4),
                                 "p_home_raw": round(h, 4),
                                 "p_draw_raw": round(d, 4),
                                 "p_away_raw": round(a, 4),
                                 "letdraw_guard": letdraw_guard(
                                     h2, d2, a2, int(handicap))}

    # 冷门比分：第二可能结果中概率最高的比分
    order = sorted(range(3), key=lambda i: p_final[i], reverse=True)
    upset_outcome = order[1]
    all_scores = [({"score": s, "prob": round(p, 4)})
                  for s, p in top_scores(matrix_cal, n=12)]
    upset_cands = [s for s in all_scores
                   if _score_winner(s["score"]) == upset_outcome]
    deriv["upset_score"] = upset_cands[0] if upset_cands else None

    # ---- 10. 一致性检查 ----
    issues = consistency_check((p_home, p_draw, p_away),
                               deriv["top_scores"], deriv["asian"],
                               deriv["over_under"])

    # ---- 11. 置信度与冷门风险 ----
    signals = [p for p in (p_model, p_market, p_elo) if p is not None]
    drift_n = (len(market["drift"]["signals"])
               if market and market.get("drift") else 0)
    conf_score = _confidence_score(
        score, signals, market["overround"] if market else 0.0,
        bool(payload.get("injury")), issues, drift_signals=drift_n)
    confidence = {"S": "S", "A": "A", "B": "B", "C": "C"}[grade]
    if lam_notes.get("degraded") or issues or conf_score < 50:
        confidence = {"S": "A", "A": "B", "B": "C", "C": "C"}[confidence]
    risk, risk_factors = _upset_risk((p_home, p_draw, p_away),
                                     deriv["top_scores"], movement, p_draw)

    # ---- 11b. 模型/市场背离门控 ----
    # 模型信号与市场信号的首选方向不一致，且差距超过阈值 → 自动警告并降档。
    # 15 场复盘中两次严重背离（藤枝 MYFC、大阪樱花；卢森堡/冰岛）最终都是市场
    # 方向获胜，因此背离时不硬扛：自动降一档并标记。
    divergence = None
    gate = cfg["divergence_gate"]


    def _divergence_gate(p_mod, p_mkt):
        """同一套门控逻辑：首选方向不一致且 gap >= gate → 返回 divergence dict。"""
        names3 = ("home", "draw", "away")
        m_dir = max(range(3), key=lambda i: p_mod[i])
        k_dir = max(range(3), key=lambda i: p_mkt[i])
        gap = abs(p_mod[m_dir] - p_mkt[m_dir])
        if m_dir != k_dir and gap >= gate:
            return {
                "model_direction": names3[m_dir],
                "market_direction": names3[k_dir],
                "gap": round(gap, 4),
            }
        return None

    if gate > 0 and p_market is not None:
        divergence = _divergence_gate(p_model, p_market)
    elif gate > 0:
        # v2.5b fallback：胜平负未开售（p_market 为 None）但有官方让球SP时，
        # 用让球口径跑同一套门控：模型校准后 handicap_1x2 vs 让球SP去水概率。
        # 2026-10-06 竞彩009（瑞士vs北马其顿）教训：模型让负84% vs 市场让负25.3%
        # 差59个点方向完全相反，原门控对此类场次完全失明。
        # payload 新字段：handicap_sp=[让胜SP,让平SP,让负SP]，对应 handicap_line。
        # 组装位置见 docs/payload-fields.md；调用方在拿到官方让球SP时传入。
        hsp = payload.get("handicap_sp")
        hcap = deriv.get("handicap_1x2")
        o = None
        if hsp and hcap:
            try:
                o = (float(hsp[0]), float(hsp[1]), float(hsp[2]))
            except (TypeError, ValueError, IndexError):
                o = None
        if o is not None:
            try:
                p_hcap = shin_probs(o)
            except Exception:
                p_hcap = implied_proportional(o)
            p_mod_hcap = (float(hcap["p_home"]), float(hcap["p_draw"]),
                          float(hcap["p_away"]))
            divergence = _divergence_gate(p_mod_hcap, p_hcap)
            if divergence is not None:
                divergence["market"] = "handicap"
                divergence["handicap_line"] = hcap.get("line")

    # v2.6 独立模型信号分歧（AF predictions）：模型 vs 模型，非市场分歧。
    # payload 可选字段 af_pred=[pH,pD,pA]（0-1，第三方模型三向概率）。
    # 在胜平负校准后（p_home/p_draw/p_away 终值）比较：引擎首选 vs AF首选，
    # 方向不一致且 gap>=gate → 触发，沿用B补丁门控口径（降一档/conf-15/risk+15）。
    # 与市场门控互斥：市场门控（含让球fallback）已触发时不再重复触发。
    if gate > 0 and divergence is None:
        afp = payload.get("af_pred")
        af = None
        if afp:
            try:
                af = (float(afp[0]), float(afp[1]), float(afp[2]))
            except (TypeError, ValueError, IndexError):
                af = None
        if af is not None and sum(af) > 0:
            _t = sum(af)
            af = (af[0] / _t, af[1] / _t, af[2] / _t)
            d = _divergence_gate((p_home, p_draw, p_away), af)
            if d is not None:
                divergence = {
                    "signal": "af_model",
                    "model_direction": d["model_direction"],
                    "af_direction": d["market_direction"],
                    "gap": d["gap"],
                }

    if divergence is not None:
        confidence = {"S": "A", "A": "B", "B": "C", "C": "C"}[confidence]
        conf_score = round(max(conf_score - 15.0, 0.0), 1)
        risk = round(min(risk + 15.0, 100.0), 1)
        _sig = divergence.get("signal")
        _mkt = divergence.get("market")
        risk_factors.append(
            "独立模型信号分歧（AF）" if _sig == "af_model"
            else ("模型与让球市场严重背离" if _mkt == "handicap"
                  else "模型与市场严重背离"))

    # ---- 12. Monte Carlo（门控） ----
    mc = maybe_simulate(lam_h, lam_a, completeness=score,
                        min_score=cfg["mc_min_score"], n=cfg["mc_n"])

    probs = {"home": p_home, "draw": p_draw, "away": p_away}
    fav = max(probs, key=probs.get)
    recommendation = {
        "direction": {"home": "主胜", "draw": "平局", "away": "客胜"}[fav],
        "prob": probs[fav],
        "value_outcomes": [v["outcome"] for v in value],
    }

    # ---- 北单适配层（model='beidan' 时启用，与竞彩链路隔离） ----
    beidan_out = {}
    if model == "beidan":
        # 北单专属Platt校准：对胜平负首选、让球首选、总进球首选分别校准
        p_top_sp = max(p_home, p_draw, p_away)
        beidan_out["calibrated_p_top_sp"] = round(apply_beidan_calibration("sp", p_top_sp), 4)
        # 让球首选概率（从 handicap_1x2 取）
        try:
            h1x2 = deriv.get("handicap_1x2", {})
            p_top_rq = max(h1x2.get("p_home", 0), h1x2.get("p_draw", 0), h1x2.get("p_away", 0))
        except Exception:
            p_top_rq = p_top_sp
        beidan_out["calibrated_p_top_rq"] = round(apply_beidan_calibration("rq", p_top_rq), 4)
        # 冷门分层：翻车风险分
        rq_val = 0
        try:
            rq_val = int(payload.get("handicap", 0) or 0)
        except (ValueError, TypeError):
            rq_val = 0
        b_risk = beidan_upset_risk(
            p_model_top=p_top_sp,
            handicap=rq_val,
            league=payload.get("competition", "") or payload.get("league", ""),
        )
        beidan_out["upset_risk"] = b_risk
        beidan_out["upset_risk_tier"] = beidan_risk_tier(b_risk)
        beidan_out["playtypes"] = ["胜平负", "让球胜平负", "比分", "总进球", "半全场", "上下单双"]

    return {
        "status": "ok",
        "model": model,
        "model_version": version,
        "match": {"home": home, "away": away,
                  "competition": payload.get("competition", ""),
                  "kickoff_at": payload["kickoff_at"]},
        "grade": grade, "completeness": score,
        "lambda_home": round(lam_h, 3), "lambda_away": round(lam_a, 3),
        "lambda_notes": lam_notes,
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "signals": {
            "model": [round(p, 4) for p in p_model],
            "market": ([round(p, 4) for p in p_market]
                       if p_market else None),
            "elo": ([round(p, 4) for p in p_elo] if p_elo else None),
        },
        "weights": {k: round(v, 3) for k, v in weights.items()},
        "flow": flow,
        "flow_shadow": flow_shadow,
        "market": market,
        "value": value,
        "derivatives": deriv,
        "consistency_issues": issues,
        "confidence": confidence,
        "confidence_score": conf_score,
        "divergence": divergence,
        "upset_risk": risk,
        "upset_risk_factors": risk_factors,
        "beidan": beidan_out,
        "monte_carlo": mc,
        "calibrated": platt_on,
        "recommendation": recommendation,
        "notes": [
            "概率为赛前估计，非结果承诺；单比分概率通常较低，仅供覆盖参考。",
            "伤停修正仅在已确认名单时生效，未经确认不做调整。" if not payload.get("injury")
            else f"已应用伤停修正: {payload['injury']}",
        ] + (["已应用回测拟合的 Platt 概率校准。"] if platt_on else [])
        + (["一致性检查发现冲突，信心已下调。"] if issues else [])
        + ([_divergence_note(divergence)] if divergence else []),
    }


def _divergence_note(d):
    """divergence 展示文案：区分市场分歧 / 让球分歧 / 独立模型分歧。"""
    if d.get("signal") == "af_model":
        return (f"⚠️独立模型信号分歧（本模型看{d['model_direction']}、"
                f"AF模型看{d['af_direction']}），信心已自动下调；"
                "分歧时不硬扛。")
    who = "让球市场" if d.get("market") == "handicap" else "市场"
    return (f"⚠️模型与{who}严重背离（模型看{d['model_direction']}、"
            f"{who}看{d['market_direction']}），信心已自动下调；"
            "背离时不硬扛。")
