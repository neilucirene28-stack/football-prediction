"""竞彩专用计算核心，由v11竞彩路径独立提取；不导入旧双模式预测器。"""

from .errors import PredictError
from .jingcai_scope import validated_config
from .jingcai_config import default_config, ENGINE_VERSION, WEAK_COMPETITIONS
from datetime import datetime
import hashlib
import json
from .elo import win_probability_from_elo
from .strengths import estimate_lambdas, team_tier
from .poisson import (
    score_matrix,
    match_probs,
    btts_prob,
    over_under_prob,
    asian_handicap_probs,
    handicap_1x2,
    top_scores,
    top_scores_diverse,
    total_goals_distribution,
    expected_total_goals,
    main_goal_interval,
    half_time_probs,
    half_full_1x2,
    total_goals_exact,
    ipf_to_marginals,
)
from .market import (
    implied_proportional,
    shin_probs,
    overround,
    kelly_fraction,
    market_drift,
    fair_handicap,
    handicap_movement,
)
from .fusion import (
    completeness_score,
    ensemble,
    ensemble_weights,
    agreement,
    fuse_probs,
)
from .backtest import platt_apply
from .market_flow import flow_features, apply_volume_weight, movement_features
from .jingcai_letdraw import (
    apply_letdraw_to_matrix,
    letdraw_guard,
    TIER_PRIOR,
    LEAGUE_PRIOR_1,
)
from .jingcai_handicap import (
    integer_handicap,
    handicap_view,
    matrix_summary,
    PIPELINE_REVISION,
    LETDRAW_FIT_BEFORE,
)
from .jingcai_inputs import validate_config, validate_payload, aware_datetime
from .jingcai_grid import score_grid, validate_dc, TAIL_TOLERANCE
from .jingcai_ledger import input_digest
from .jingcai_calibration import validate_provenance
from .jingcai_cards import (
    predict_cards as predict_jingcai_cards,
    CARDS_REVISION,
    load_context as load_jingcai_cards_context,
)
from .jingcai_montecarlo import simulate_matrix


def model_version(cfg: dict) -> str:
    """模型版本号：由全部被跟踪参数计算得出，参数一变版本即变。

    杜绝"3 月和 8 月的预测被混成一个系统"——回测/学习时必须按版本分组。
    """
    canon = json.dumps(cfg, sort_keys=True, default=str)
    digest = hashlib.sha1(canon.encode("utf-8")).hexdigest()[:8]
    return f"v{ENGINE_VERSION}+{digest}"


def _recent_ok(rows) -> bool:
    return (
        isinstance(rows, list)
        and len(rows) >= 6
        and all(
            (
                isinstance(r, dict)
                and isinstance(r.get("gf"), int)
                and isinstance(r.get("ga"), int)
                for r in rows
            )
        )
    )


def _score_winner(score: str) -> int:
    i, j = (int(x) for x in score.split("-"))
    return 0 if i > j else 1 if i == j else 2


def _goal_interval_probs(matrix) -> dict:
    """总进球三档区间概率：0-1球 / 2-3球 / 4+球。

    v2.10：比分分布呈现。Poisson众数天然偏小，单点比分误导性强；
    三档区间展示分布形状，不改变任何概率计算。
    """
    dist = total_goals_distribution(matrix)
    p01 = dist.get(0, 0.0) + dist.get(1, 0.0)
    p23 = dist.get(2, 0.0) + dist.get(3, 0.0)
    p4p = dist.get(4, 0.0) + dist.get("5+", 0.0)
    return {"0-1球": round(p01, 4), "2-3球": round(p23, 4), "4+球": round(p4p, 4)}


def consistency_check(p_final, top3, asian, ou, *, check_modal_score=True) -> list[str]:
    """十七节：所有市场必须方向一致，否则报告冲突。"""
    issues = []
    fav = max(range(3), key=lambda i: p_final[i])
    w0 = _score_winner(top3[0]["score"])
    if check_modal_score and w0 != fav and (p_final[fav] - p_final[w0] > 0.08):
        issues.append(f"首选比分 {top3[0]['score']} 的胜负方向与 1X2 首选不一致")
    if asian and fav == 0 and (asian["win"] < 0.35):
        issues.append("1X2 看好主胜但亚盘赢盘概率过低，方向存疑")
    if asian and fav == 2 and (asian["lose"] < 0.35):
        issues.append("1X2 看好客胜但亚盘输盘概率过低，方向存疑")
    if (
        check_modal_score
        and ou
        and (ou["over"] > 0.6)
        and (top3[0]["prob"] > 0)
        and (sum((int(x) for x in top3[0]["score"].split("-"))) <= 1)
    ):
        issues.append("大小球倾向大球但首选比分为小比分，进球模型冲突")
    return issues


def _confidence_score(
    completeness, signals, overround_v, has_lineup, issues, drift_signals: int = 0
) -> float:
    """十八节：Confidence = 数据质量 × 模型一致 × 市场稳定 × 阵容确定。"""
    dq = completeness / 100.0
    ag = agreement([s for s in signals if s is not None])
    stability = 1.0 - min(max(overround_v, 0.0), 0.2) / 0.2 * 0.3
    stability *= 1.0 - 0.1 * min(drift_signals, 2)
    lineup = 1.0 if has_lineup else 0.6
    conf = 100.0 * dq * ag * stability * lineup
    if issues:
        conf *= 0.9
    return round(max(min(conf, 100.0), 0.0), 1)


def _upset_risk(p_final, top3, movement, p_draw) -> tuple[float, list[str]]:
    """十三节：冷门风险 0-100（多因子），不单纯看赔率。"""
    risk = 100.0 * (1 - max(p_final))
    factors = []
    if p_draw > 0.3:
        risk += 8
        factors.append("平局概率异常高")
    small_conc = sum(
        (s["prob"] for s in top3 if sum((int(x) for x in s["score"].split("-"))) <= 1)
    )
    if small_conc > 0.3:
        risk += 5
        factors.append("小比分概率集中")
    if movement and movement.get("signal") == "背离":
        risk += 10
        factors.append("模型与盘口背离")
    if max(p_final) < 0.45:
        risk += 5
        factors.append("实力接近、无明显热门")
    return (round(max(min(risk, 100.0), 0.0), 1), factors)


def predict(
    payload: dict, config: dict | None = None, asof: datetime | None = None
) -> dict:
    """竞彩专用计算核心；asof只用于明确标记的历史回放。"""
    config = validated_config(payload, config)
    cfg = default_config()
    cfg.update(config or {})
    cards_context = None
    try:
        validate_config(cfg)
    except ValueError as e:
        raise PredictError(str(e)) from e
    cfg["jingcai_pipeline_revision"] = PIPELINE_REVISION
    cfg["jingcai_cards_revision"] = CARDS_REVISION
    cards_context = load_jingcai_cards_context()
    cfg["jingcai_cards_data_profile"] = cards_context["profile"]
    cfg.setdefault("mc_seed", 0)
    if isinstance(cfg["mc_seed"], bool) or not isinstance(cfg["mc_seed"], int):
        raise PredictError("竞彩mc_seed必须为整数")
    cfg["letdraw_prior_profile"] = {
        "fit_before": LETDRAW_FIT_BEFORE,
        "tier": TIER_PRIOR,
        "league": LEAGUE_PRIOR_1,
    }
    base_version = None
    base_cfg = {k: v for k, v in cfg.items() if k != "platt_provenance"}
    base_cfg["platt"] = None
    base_version = model_version(base_cfg)
    version = model_version(cfg)
    if not isinstance(payload, dict):
        raise PredictError("payload 必须为对象")
    original_input_digest = input_digest(payload)
    try:
        parse_time = aware_datetime
        kickoff = parse_time(payload["kickoff_at"], "kickoff_at")
        snapshot = parse_time(payload["snapshot_at"], "snapshot_at")
    except KeyError as e:
        raise PredictError(f"缺少必填时间字段: {e}")
    except ValueError as e:
        raise PredictError(str(e)) from e
    now = asof if asof is not None else datetime.now().astimezone()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PredictError("asof 必须为带时区的datetime")
    if kickoff <= now:
        raise PredictError("比赛已开赛或为历史比赛：拒绝赛后预测")
    if snapshot > kickoff:
        raise PredictError("快照晚于开球：存在未来泄漏")
    if snapshot > now:
        raise PredictError("快照晚于预测截止时刻：存在未来泄漏")
    input_audit = None
    try:
        payload, input_audit = validate_payload(payload, snapshot)
    except ValueError as e:
        raise PredictError(str(e)) from e
    calibration_audit = {"status": "not_requested"}
    if cfg.get("platt"):
        try:
            calibration_audit = validate_provenance(
                cfg.get("platt_provenance"),
                base_version=base_version,
                asof=now,
                has_market=bool(payload.get("odds")),
                has_elo=bool(payload.get("elo")),
            )
        except ValueError as e:
            raise PredictError(str(e)) from e
    handicap = payload.get("handicap_line")
    if handicap is not None:
        try:
            handicap = integer_handicap(handicap)
        except ValueError as e:
            raise PredictError(str(e)) from e
        if (
            handicap != 0
            and cfg.get("letdraw_strength", 0.5) > 0
            and (now < aware_datetime(LETDRAW_FIT_BEFORE, "letdraw_fit_before"))
        ):
            raise PredictError(
                "固定让平先验训练于2026-09，不能用于训练期内回测；请关闭letdraw_strength或使用另行验证的时序校准"
            )
    home, away = (payload.get("home"), payload.get("away"))
    if (
        not home
        or not away
        or home == away
        or (
            not isinstance(home, str)
            or not isinstance(away, str)
            or (not home.strip())
            or (not away.strip())
        )
    ):
        raise PredictError("主客队无效")
    home_recent = payload.get("home_recent") or []
    away_recent = payload.get("away_recent") or []
    league_avg = float(payload.get("league_avg_goals", 2.7))
    present = {
        "form": _recent_ok(home_recent) and _recent_ok(away_recent),
        "venue_split": any((r.get("venue") for r in home_recent + away_recent)),
        "odds": bool(payload.get("odds")),
        "asian": bool(payload.get("asian")),
        "ou": payload.get("ou_line") is not None,
        "lineup": bool(payload.get("injury")),
        "advanced": False,
    }
    score, grade = completeness_score(present)
    if grade == "D":
        return {
            "status": "insufficient_data",
            "grade": grade,
            "completeness": score,
            "reason": "数据完整度不足（D级），拒绝给出概率预测",
            **{"input_audit": input_audit},
        }
    neutral = bool(payload.get("neutral_site"))
    ht, at_ = (team_tier(home_recent), team_tier(away_recent))
    competition = payload.get("competition", "")
    is_weak = competition in WEAK_COMPETITIONS
    user_cfg = config or {}
    if is_weak and "shrink_prior" not in user_cfg and cfg.get("weak_shrink_prior"):
        eff_shrink = cfg["weak_shrink_prior"]
    else:
        eff_shrink = cfg["shrink_prior"]
    lam_h, lam_a, lam_notes = estimate_lambdas(
        home_recent,
        away_recent,
        league_avg,
        injury=payload.get("injury"),
        decay=cfg["decay"],
        home_adv_factor=1.0 if neutral else 1.12,
        home_tier=ht,
        away_tier=at_,
        shrink_prior=eff_shrink,
    )
    lam_notes["home_adv_factor"] = 1.0 if neutral else 1.12
    lam_notes["neutral_site"] = neutral
    lam_notes["weak_competition"] = is_weak
    if is_weak:
        lam_notes["shrink_prior_effective"] = eff_shrink
    h2h = payload.get("h2h") or []
    if h2h:
        recent_h2h = h2h[:3]
        gd = sum((r["gf"] - r["ga"] for r in recent_h2h))
        adj = max(min(gd * 0.01, 0.03), -0.03)
        lam_h *= 1 + adj
        lam_a *= 1 - adj
        lam_notes["h2h_adjust"] = round(adj, 4)
    weak_cap = cfg.get("weak_goal_cap") or 0
    if is_weak and weak_cap > 0 and (lam_h + lam_a > weak_cap):
        scale = weak_cap / (lam_h + lam_a)
        lam_h *= scale
        lam_a *= scale
        lam_notes["weak_goal_cap_applied"] = {"cap": weak_cap, "scale": round(scale, 4)}
    max_goals, tail_mass = (10, None)
    try:
        validate_dc(lam_h, lam_a, cfg["rho"])
        max_goals, tail_mass = score_grid(lam_h, lam_a)
    except ValueError as e:
        raise PredictError(str(e)) from e
    matrix = score_matrix(lam_h, lam_a, rho=cfg["rho"], max_goals=max_goals)
    p_model = match_probs(matrix)
    p_elo = None
    if payload.get("elo"):
        e = payload["elo"]
        p_elo = win_probability_from_elo(float(e["home"]), float(e["away"]))
    p_market = market = None
    odds = payload.get("odds")
    o = None
    if odds:
        o = (float(odds["home"]), float(odds["draw"]), float(odds["away"]))
        try:
            p_market = shin_probs(o)
        except Exception:
            p_market = implied_proportional(o)
        market = {
            "odds": list(o),
            "overround": round(overround(o), 4),
            "implied": [round(p, 4) for p in p_market],
        }
        if payload.get("opening_odds"):
            oo = payload["opening_odds"]
            market["drift"] = market_drift(
                (float(oo["home"]), float(oo["draw"]), float(oo["away"])), o
            )
    flow = flow_shadow = None
    _companies = (payload.get("raw") or {}).get("x12_companies")
    if _companies and p_market is not None:
        try:
            _pm = payload.get("polymarket") or {}
            flow = flow_features(_companies, pm_volume_usdc=_pm.get("volume_usdc"))
            _mv = (payload.get("raw") or {}).get("x12_movement")
            if _mv:
                flow["movement"] = movement_features(_mv, _companies)
        except Exception:
            flow = None
    weights = ensemble_weights(
        grade, p_market is not None, p_elo is not None, edge=cfg["model_edge"]
    )
    p_final = ensemble({"model": p_model, "market": p_market, "elo": p_elo}, weights)
    if flow is not None:
        w_flow = apply_volume_weight(weights, flow["volume_weight"])
        _pf = ensemble({"model": p_model, "market": p_market, "elo": p_elo}, w_flow)
        flow_shadow = {
            "weights": {k: round(v, 4) for k, v in w_flow.items()},
            "p_home": round(_pf[0], 4),
            "p_draw": round(_pf[1], 4),
            "p_away": round(_pf[2], 4),
        }
    platt_on = False
    if cfg.get("platt"):
        cal = [
            platt_apply(p, *cfg["platt"][k])
            for p, k in zip(p_final, ("home", "draw", "away"))
        ]
        s = sum(cal)
        if s > 0:
            p_final = tuple((c / s for c in cal))
            platt_on = True
    p_home, p_draw, p_away = (round(p, 4) for p in p_final)
    matrix_cal = ipf_to_marginals(matrix, p_final)
    matrix_pre_letdraw = matrix_cal
    matrix_ld = None
    if handicap is not None:
        matrix_ld = apply_letdraw_to_matrix(
            matrix_pre_letdraw,
            handicap,
            league=competition,
            strength=cfg.get("letdraw_strength", 0.5),
        )
        matrix_cal = matrix_ld
    value = []
    probability_differences = []
    if o and market:
        names = ("home", "draw", "away")
        for idx, (name, p, odd) in enumerate(zip(names, (p_home, p_draw, p_away), o)):
            p = p_final[idx]
            implied = p_market[idx]
            edge = round(p - implied, 4)
            kelly = round(kelly_fraction(p, odd, cfg["kelly_fraction"]), 4)
            if edge > 0.05:
                item = {"outcome": name, "edge": edge, "kelly": kelly}
                ev = p * odd - 1.0
                item.update(
                    {
                        "model_probability": p,
                        "market_probability": implied,
                        "offered_odds": odd,
                        "fixed_odds_expected_net": ev,
                        "validation_status": "unvalidated",
                    }
                )
                probability_differences.append(dict(item))
                if ev <= 0:
                    continue
                value.append(item)
    ou_line = payload.get("ou_line")
    fair = fair_handicap(matrix_cal)
    half_full = half_full_1x2(
        lam_h,
        lam_a,
        ht_factor=cfg["ht_factor"],
        rho=cfg["rho"],
        ft_matrix=matrix_cal,
        strict_ft_grid=True,
    )
    deriv = {
        "btts": round(btts_prob(matrix_cal), 4),
        "over_under": None,
        "asian": None,
        "handicap_1x2": None,
        "top_scores": [
            {"score": s, "prob": round(p, 4)}
            for s, p in top_scores_diverse(matrix_cal, n=5, lam_h=lam_h, lam_a=lam_a)
        ],
        "top_scores_raw": [
            {"score": s, "prob": round(p, 4)} for s, p in top_scores(matrix, n=5)
        ],
        "p_1x2_raw": [round(p, 4) for p in p_model],
        "total_goals": {
            str(k): round(v, 4) for k, v in total_goals_distribution(matrix_cal).items()
        },
        "goal_interval_probs": _goal_interval_probs(matrix_cal),
        "expected_goals": round(expected_total_goals(matrix_cal), 2),
        "expected_goals_raw": round(expected_total_goals(matrix), 2),
        "main_goal_interval": None,
        "fair_handicap": fair,
        "cards": None,
        "half_time": half_time_probs(
            lam_h, lam_a, ht_factor=cfg["ht_factor"], rho=cfg["rho"]
        ),
        "half_full_1x2": {k: round(v, 4) for k, v in half_full.items()},
        "half_full_1x2_full": half_full,
        "total_goals_exact": {
            str(k): round(v, 4) for k, v in total_goals_exact(matrix_cal).items()
        },
    }
    interval, interval_p = main_goal_interval(matrix_cal)
    deriv["btts_full"] = btts_prob(matrix_cal)
    deriv["expected_goals_full"] = expected_total_goals(matrix_cal)
    deriv["total_goals_exact_full"] = {
        str(k): v for k, v in total_goals_exact(matrix_cal).items()
    }
    names = ("home", "draw", "away")
    ht_probs = [
        sum((v for label, v in half_full.items() if label[0] == symbol))
        for symbol in ("胜", "平", "负")
    ]
    deriv["half_time_full"] = dict(zip(("home", "draw", "away"), ht_probs))
    deriv["half_time_raw"] = deriv["half_time"]
    deriv["half_time"] = {
        "ht_factor": cfg["ht_factor"],
        **{f"p_{name}": round(v, 4) for name, v in zip(names, ht_probs)},
        "probability_source": "jingcai_half_full_joint_distribution",
        "raw_model_top_scores": deriv["half_time_raw"]["top_scores"],
    }
    try:
        deriv["cards"] = predict_jingcai_cards(payload, snapshot, context=cards_context)
    except (ValueError, TypeError, KeyError) as e:
        raise PredictError(f"竞彩牌数输入无效：{e}") from e
    deriv["main_goal_interval"] = {"label": interval, "prob": interval_p}
    if ou_line is not None:
        over, under = over_under_prob(matrix_cal, float(ou_line))
        deriv["over_under"] = {
            "line": ou_line,
            "over": round(over, 4),
            "under": round(under, 4),
        }
    asian = payload.get("asian")
    movement = None
    if asian and asian.get("handicap") is not None:
        win, push, lose = asian_handicap_probs(matrix_cal, float(asian["handicap"]))
        deriv["asian"] = {
            "handicap": asian["handicap"],
            "win": round(win, 4),
            "push": round(push, 4),
            "lose": round(lose, 4),
            "fair_handicap": fair["handicap"],
            "line_bias": round(float(asian["handicap"]) - fair["handicap"], 2),
        }
        if asian.get("opening_handicap") is not None:
            movement = handicap_movement(
                {
                    "handicap": float(asian["opening_handicap"]),
                    "home_water": asian.get("opening_home_water"),
                },
                {
                    "handicap": float(asian["handicap"]),
                    "home_water": asian.get("home_water"),
                },
                p_home,
            )
            deriv["asian"]["movement"] = movement
    if handicap is not None:
        h, d, a = handicap_1x2(matrix_pre_letdraw, int(handicap))
        if matrix_ld is None:
            matrix_ld = apply_letdraw_to_matrix(
                matrix_cal,
                int(handicap),
                league=competition,
                strength=cfg.get("letdraw_strength", 0.5),
            )
        h2, d2, a2 = handicap_1x2(matrix_ld, int(handicap))
        _hcp_pick = max([("home", h2), ("draw", d2), ("away", a2)], key=lambda x: x[1])[
            0
        ]
        deriv["handicap_1x2"] = {
            "line": handicap,
            "p_home": round(h2, 4),
            "p_draw": round(d2, 4),
            "p_away": round(a2, 4),
            "p_home_raw": round(h, 4),
            "p_draw_raw": round(d, 4),
            "p_away_raw": round(a, 4),
            "p_home_full": h2,
            "p_draw_full": d2,
            "p_away_full": a2,
            "pick": _hcp_pick,
            "pick_rule": "argmax",
            "letdraw_guard": letdraw_guard(h2, d2, a2, int(handicap)),
        }
        deriv["handicap_1x2"]["risk_view"] = handicap_view((h2, d2, a2), handicap)
        deriv["handicap_1x2"]["probability_source"] = "jingcai_final_score_matrix"
        deriv["handicap_1x2"]["probability_trace"] = {
            "rho": cfg["rho"],
            "strength": cfg.get("letdraw_strength", 0.5),
            "max_goals": len(matrix) - 1,
            "poisson_tail_mass_before_normalization": tail_mass,
            "poisson_tail_tolerance": TAIL_TOLERANCE,
            "prior_fit_before": LETDRAW_FIT_BEFORE,
            "poisson": list(
                handicap_1x2(
                    score_matrix(lam_h, lam_a, rho=0, max_goals=max_goals), handicap
                )
            ),
            "dc": list(handicap_1x2(matrix, handicap)),
            "ipf": [h, d, a],
            "final": [h2, d2, a2],
            "matrix_before": matrix_summary(matrix_pre_letdraw),
            "matrix_after": matrix_summary(matrix_cal),
            "wdl_before": list(match_probs(matrix_pre_letdraw)),
            "wdl_after": list(match_probs(matrix_cal)),
        }
    order = sorted(range(3), key=lambda i: p_final[i], reverse=True)
    upset_outcome = order[1]
    all_scores = [
        {"score": s, "prob": round(p, 4)} for s, p in top_scores(matrix_cal, n=12)
    ]
    upset_cands = [s for s in all_scores if _score_winner(s["score"]) == upset_outcome]
    deriv["upset_score"] = upset_cands[0] if upset_cands else None
    issues = consistency_check(
        (p_home, p_draw, p_away),
        deriv["top_scores"],
        deriv["asian"],
        deriv["over_under"],
        check_modal_score=False,
    )
    signals = [p for p in (p_model, p_market, p_elo) if p is not None]
    drift_n = len(market["drift"]["signals"]) if market and market.get("drift") else 0
    lineup_audit = None
    has_confirmed_lineup = bool(payload.get("injury"))
    from .jingcai_reporting import lineup_confirmation, score_explanation

    lineup_audit = lineup_confirmation(input_audit)
    has_confirmed_lineup = lineup_audit["both_confirmed"]
    deriv["score_explanation"] = score_explanation(matrix_cal, p_final)
    conf_score = _confidence_score(
        score,
        signals,
        market["overround"] if market else 0.0,
        has_confirmed_lineup,
        issues,
        drift_signals=drift_n,
    )
    confidence = {"S": "S", "A": "A", "B": "B", "C": "C"}[grade]
    if lam_notes.get("degraded") or issues or conf_score < 50:
        confidence = {"S": "A", "A": "B", "B": "C", "C": "C"}[confidence]
    risk, risk_factors = _upset_risk(
        (p_home, p_draw, p_away), deriv["top_scores"], movement, p_draw
    )
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
            p_mod_hcap = (
                float(hcap["p_home"]),
                float(hcap["p_draw"]),
                float(hcap["p_away"]),
            )
            divergence = _divergence_gate(p_mod_hcap, p_hcap)
            if divergence is not None:
                divergence["market"] = "handicap"
                divergence["handicap_line"] = hcap.get("line")
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
            "独立模型信号分歧（AF）"
            if _sig == "af_model"
            else (
                "模型与让球市场严重背离" if _mkt == "handicap" else "模型与市场严重背离"
            )
        )
    mc = simulate_matrix(
        matrix_cal,
        completeness=score,
        min_score=cfg["mc_min_score"],
        n=cfg["mc_n"],
        seed=cfg["mc_seed"],
    )
    probs = {"home": p_home, "draw": p_draw, "away": p_away}
    fav = max(probs, key=probs.get)
    recommendation = {
        "direction": {"home": "主胜", "draw": "平局", "away": "客胜"}[fav],
        "prob": probs[fav],
        "value_outcomes": [v["outcome"] for v in value],
    }
    out = {
        "status": "ok",
        "model": "jingcai",
        "model_version": version,
        "match": {
            "home": home,
            "away": away,
            "competition": payload.get("competition", ""),
            "kickoff_at": payload["kickoff_at"],
        },
        "grade": grade,
        "completeness": score,
        "lambda_home": round(lam_h, 3),
        "lambda_away": round(lam_a, 3),
        "lambda_notes": lam_notes,
        "p_home": p_home,
        "p_draw": p_draw,
        "p_away": p_away,
        "signals": {
            "model": [round(p, 4) for p in p_model],
            "market": [round(p, 4) for p in p_market] if p_market else None,
            "elo": [round(p, 4) for p in p_elo] if p_elo else None,
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
            (
                "伤停修正仅在已确认名单时生效，未经确认不做调整。"
                if not payload.get("injury")
                else f"已应用伤停修正: {payload['injury']}"
            ),
        ]
        + (["已应用回测拟合的 Platt 概率校准。"] if platt_on else [])
        + (["一致性检查发现冲突，信心已下调。"] if issues else [])
        + ([_divergence_note(divergence)] if divergence else []),
    }
    generated_at = datetime.now().astimezone()
    if asof is None and generated_at >= kickoff:
        raise PredictError("预测计算完成时比赛已开球，拒绝封存赛前预测")
    out["prediction_generated_at"] = generated_at.isoformat()
    out["feature_cutoff_at"] = payload["snapshot_at"]
    out["evaluation_asof"] = now.isoformat()
    out["evaluation_mode"] = "historical_replay" if asof is not None else "live"
    out["pipeline_revision"] = PIPELINE_REVISION
    out["input_sha256"] = original_input_digest
    out["base_model_version"] = base_version
    out["calibration_audit"] = calibration_audit
    out["signals_full"] = {
        "model": list(p_model),
        "market": list(p_market) if p_market is not None else None,
        "elo": list(p_elo) if p_elo is not None else None,
    }
    out["input_audit"] = input_audit
    out["confidence_audit"] = {
        "kind": "heuristic_data_quality_score_not_hit_probability",
        "lineup": lineup_audit,
        "lineup_factor": 1.0 if has_confirmed_lineup else 0.6,
        "grade_policy": "legacy_completeness_grade_with_risk_downgrades",
        "qualification": "not empirically calibrated; confidence letter is not a score bucket",
    }
    out["notes"].append(
        "置信评分衡量数据质量与信号一致性，不代表命中概率；字母等级沿用完整度和风险降档规则。"
    )
    out["team_intelligence"] = input_audit["team_intelligence"]
    out["team_observations"] = input_audit["team_observations"]
    if out["team_observations"]["status"] in ("ok", "partial"):
        out["notes"].append(
            "已校验竞彩多源情报并输出分来源特征；缺失保留未知，概率影响系数尚未启用。"
        )
    if out["team_intelligence"]["status"] in ("missing", "partial"):
        out["notes"].append("球队/外援阵容情报缺失或仅覆盖一方，未作概率修正。")
    else:
        out["notes"].append("已记录球队/外援阵容情报；影响系数待验证，未作概率修正。")
    hsp_state = input_audit["optional_signal_status"]["handicap_sp"]
    hsp = payload.get("handicap_sp")
    out["handicap_market_diagnostic"] = {
        "status": hsp_state["status"],
        "probability_fusion": False,
    }
    if hsp:
        try:
            hmarket = shin_probs(tuple(hsp))
            method = "shin"
        except Exception:
            hmarket = implied_proportional(tuple(hsp))
            method = "proportional_fallback"
        hcap = deriv.get("handicap_1x2")
        out["handicap_market_diagnostic"].update(
            probabilities_full=list(hmarket),
            method=method,
            available_at=hsp_state.get("available_at"),
            source=hsp_state.get("source"),
            provenance_status=hsp_state["provenance_status"],
            line=hcap.get("line") if hcap else None,
            divergence=(
                _divergence_gate(
                    [hcap[f"p_{k}_full"] for k in ("home", "draw", "away")], hmarket
                )
                if hcap and gate > 0
                else None
            ),
            used_for_divergence_gate=bool(p_market is None and hcap and (gate > 0)),
            diagnostic_only=not bool(p_market is None and hcap and (gate > 0)),
        )
    if hsp_state["status"] == "excluded":
        out["notes"].append("让球赔率未使用：" + hsp_state["reason"])
    elif hsp_state["status"] == "accepted" and (not hsp_state.get("available_at")):
        out["notes"].append("让球赔率缺可得时间；可作诊断，不能视为已核验赛前数据。")
    out["score_grid"] = {
        "max_goals": max_goals,
        "poisson_tail_mass_before_normalization": tail_mass,
        "poisson_tail_tolerance": TAIL_TOLERANCE,
    }
    out["probability_difference_candidates"] = probability_differences
    out["score_matrix_full"] = [list(row) for row in matrix_cal]
    out["p_final_full"] = [float(p) for p in p_final]
    out["lambda_home_full"] = float(lam_h)
    out["lambda_away_full"] = float(lam_a)
    return out


def _divergence_note(d):
    """divergence 展示文案：区分市场分歧 / 让球分歧 / 独立模型分歧。"""
    if d.get("signal") == "af_model":
        return f"⚠️独立模型信号分歧（本模型看{d['model_direction']}、AF模型看{d['af_direction']}），信心已自动下调；分歧时不硬扛。"
    who = "让球市场" if d.get("market") == "handicap" else "市场"
    return f"⚠️模型与{who}严重背离（模型看{d['model_direction']}、{who}看{d['market_direction']}），信心已自动下调；背离时不硬扛。"
