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
                      main_goal_interval, half_time_probs)
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
ENGINE_VERSION = "2.4"


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
          asof: datetime | None = None) -> dict:
    """asof: 回测用虚拟"现在"时间。生产调用必须为 None。"""
    cfg = {"rho": -0.13, "kelly_fraction": 0.25, "model_edge": 0.0,
           "decay": 0.90, "ht_factor": 0.44, "mc_n": 20000,
           "mc_min_score": 60, "platt": None,
           # 攻防评级向联赛均值收缩的先验权重（场）；0 = 关闭收缩
           "shrink_prior": 3.0,
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
    lam_h, lam_a, lam_notes = estimate_lambdas(
        home_recent, away_recent, league_avg,
        injury=payload.get("injury"), decay=cfg["decay"],
        home_adv_factor=1.0 if neutral else 1.12,
        home_tier=ht, away_tier=at_,
        shrink_prior=cfg["shrink_prior"])
    lam_notes["home_adv_factor"] = 1.0 if neutral else 1.12
    lam_notes["neutral_site"] = neutral

    h2h = payload.get("h2h") or []
    if h2h:
        # h2h 以主队视角记录：gf=主队进球。近3场净胜球 → λ 微调（±3%封顶）
        gd = sum(r["gf"] - r["ga"] for r in h2h[-3:])
        adj = max(min(gd * 0.01, 0.03), -0.03)
        lam_h *= (1 + adj)
        lam_a *= (1 - adj)
        lam_notes["h2h_adjust"] = round(adj, 4)

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

    # ---- 9. 衍生市场（同一矩阵） ----
    ou_line = payload.get("ou_line")
    fair = fair_handicap(matrix)
    deriv = {
        "btts": round(btts_prob(matrix), 4),
        "over_under": None,
        "asian": None,
        "handicap_1x2": None,
        "top_scores": [{"score": s, "prob": round(p, 4)}
                       for s, p in top_scores(matrix)],
        "total_goals": {str(k): round(v, 4)
                        for k, v in total_goals_distribution(matrix).items()},
        "expected_goals": round(expected_total_goals(matrix), 2),
        "main_goal_interval": None,
        "fair_handicap": fair,
        "cards": _cards_block(payload),
        "half_time": half_time_probs(lam_h, lam_a, ht_factor=cfg["ht_factor"],
                                     rho=cfg["rho"]),
    }
    interval, interval_p = main_goal_interval(matrix)
    deriv["main_goal_interval"] = {"label": interval, "prob": interval_p}
    if ou_line is not None:
        over, under = over_under_prob(matrix, float(ou_line))
        deriv["over_under"] = {"line": ou_line, "over": round(over, 4),
                               "under": round(under, 4)}
    asian = payload.get("asian")
    movement = None
    if asian and asian.get("handicap") is not None:
        win, push, lose = asian_handicap_probs(matrix, float(asian["handicap"]))
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
        h, d, a = handicap_1x2(matrix, int(handicap))
        deriv["handicap_1x2"] = {"line": handicap, "p_home": round(h, 4),
                                 "p_draw": round(d, 4), "p_away": round(a, 4)}

    # 冷门比分：第二可能结果中概率最高的比分
    order = sorted(range(3), key=lambda i: p_final[i], reverse=True)
    upset_outcome = order[1]
    all_scores = [({"score": s, "prob": round(p, 4)})
                  for s, p in top_scores(matrix, n=12)]
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
    if gate > 0 and p_market is not None:
        names3 = ("home", "draw", "away")
        m_dir = max(range(3), key=lambda i: p_model[i])
        k_dir = max(range(3), key=lambda i: p_market[i])
        gap = abs(p_model[m_dir] - p_market[m_dir])
        if m_dir != k_dir and gap >= gate:
            divergence = {
                "model_direction": names3[m_dir],
                "market_direction": names3[k_dir],
                "gap": round(gap, 4),
            }
            confidence = {"S": "A", "A": "B", "B": "C", "C": "C"}[confidence]
            conf_score = round(max(conf_score - 15.0, 0.0), 1)
            risk = round(min(risk + 15.0, 100.0), 1)
            risk_factors.append("模型与市场严重背离")

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

    return {
        "status": "ok",
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
        "monte_carlo": mc,
        "calibrated": platt_on,
        "recommendation": recommendation,
        "notes": [
            "概率为赛前估计，非结果承诺；单比分概率通常较低，仅供覆盖参考。",
            "伤停修正仅在已确认名单时生效，未经确认不做调整。" if not payload.get("injury")
            else f"已应用伤停修正: {payload['injury']}",
        ] + (["已应用回测拟合的 Platt 概率校准。"] if platt_on else [])
        + (["一致性检查发现冲突，信心已下调。"] if issues else [])
        + ([f"⚠️模型与市场严重背离（模型看{divergence['model_direction']}、"
             f"市场看{divergence['market_direction']}），信心已自动下调；"
             "背离时不硬扛。"] if divergence else []),
    }
