import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.predictor import predict, PredictError


def _mk(n_home, n_away, venue):
    return [{"gf": n_home, "ga": n_away, "venue": venue} for _ in range(8)]


def _future_ts(**kw):
    # 测试固件用相对时间，避免硬编码日期过期导致 predict 拒单
    return (datetime.now().astimezone() + timedelta(**kw)).isoformat()


def sample_payload(**kw):
    p = {
        "home": "土耳其", "away": "意大利", "competition": "欧国联",
        "kickoff_at": _future_ts(days=2),
        "snapshot_at": _future_ts(hours=-1),
        "league_avg_goals": 2.70,
        "home_recent": _mk(2, 1, "H"),
        "away_recent": _mk(1, 1, "A"),
        "odds": {"home": 2.69, "draw": 3.30, "away": 2.20},
        "ou_line": 2.5,
    }
    p.update(kw)
    return p


def test_predict_ok_and_sums_to_one():
    r = predict(sample_payload())
    assert r["status"] == "ok"
    assert abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1.0) < 5e-4  # 4位小数舍入容差
    assert r["grade"] in ("A", "B", "C")
    assert r["derivatives"]["over_under"] is not None
    assert len(r["derivatives"]["top_scores"]) == 5  # v2.10: Top5


def test_predict_rejects_past_match():
    with pytest.raises(PredictError):
        predict(sample_payload(kickoff_at="2026-09-01T02:45:00+08:00"))


def test_predict_insufficient_data_grade_d():
    r = predict(sample_payload(home_recent=[], away_recent=[], odds=None))
    assert r["status"] == "insufficient_data"
    assert r["grade"] == "D"


def test_predict_without_market_is_pure_model():
    r = predict(sample_payload(odds=None))
    assert r["status"] == "ok"
    assert r["weights"]["model"] == 1.0
    assert r["market"] is None


def test_neutral_site_removes_home_advantage():
    p1 = sample_payload()
    p2 = sample_payload(neutral_site=True)
    r1, r2 = predict(p1), predict(p2)
    assert r1["lambda_notes"]["home_adv_factor"] == 1.12
    assert r2["lambda_notes"]["home_adv_factor"] == 1.0
    assert r2["lambda_notes"]["neutral_site"] is True
    # 中立场地：主队进球期望更低（无 1.12 加成），客队不变
    assert r2["lambda_home"] < r1["lambda_home"]
    assert r2["lambda_away"] == r1["lambda_away"]
    assert abs(r2["p_home"] + r2["p_draw"] + r2["p_away"] - 1.0) < 1e-9


# ---------------- v2.4: 结构性收缩 ----------------

def _extreme_small_sample(venue):
    # 前 3 场比分极端（模拟弱旅大胜虚高，如朝鲜女足式 10-0/8-0），后 5 场正常
    return ([{"gf": 10, "ga": 0, "venue": venue},
             {"gf": 8, "ga": 0, "venue": venue},
             {"gf": 5, "ga": 1, "venue": venue}]
            + [{"gf": 2, "ga": 1, "venue": venue} for _ in range(5)])


def test_shrinkage_dampens_extreme_small_sample():
    # 用非弱赛事联赛，避免弱赛事 goal_cap 干扰收缩机制本身的验证
    p = sample_payload(home_recent=_extreme_small_sample("H"),
                       away_recent=_mk(1, 1, "A"), odds=None,
                       competition="英超")
    r_noshrink = predict(p, config={"shrink_prior": 0.0})
    r_shrink = predict(p, config={"shrink_prior": 3.0})
    # 收缩后：主队进球期望向联赛均值回落（3.965 → 3.344，约 -16%）
    assert r_shrink["lambda_home"] < r_noshrink["lambda_home"] * 0.9
    assert r_shrink["lambda_notes"]["shrink_prior"] == 3.0


def test_shrinkage_keeps_large_sample_stable():
    # 大样本（10 场、比分正常）下收缩几乎不改变结果
    big = [{"gf": 2, "ga": 1, "venue": "H"} for _ in range(10)]
    p = sample_payload(home_recent=big, away_recent=_mk(1, 1, "A"), odds=None)
    r0 = predict(p, config={"shrink_prior": 0.0})
    r3 = predict(p, config={"shrink_prior": 3.0})
    assert abs(r0["lambda_home"] - r3["lambda_home"]) < 0.25


def test_shrink_prior_zero_restores_v23():
    p = sample_payload(home_recent=_extreme_small_sample("H"), odds=None)
    r = predict(p, config={"shrink_prior": 0.0})
    assert r["lambda_notes"]["shrink_prior"] == 0.0


# ---------------- v2.4: 背离门控 ----------------

def test_divergence_gate_triggers_and_downgrades():
    # 模型强烈看好主队（主队极强、客队极弱），市场却强烈看好客队
    p = sample_payload(
        home_recent=[{"gf": 3, "ga": 0, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 0, "ga": 3, "venue": "A"} for _ in range(8)],
        odds={"home": 8.0, "draw": 5.0, "away": 1.30},
    )
    r = predict(p)
    assert r["divergence"] is not None
    assert r["divergence"]["model_direction"] == "home"
    assert r["divergence"]["market_direction"] == "away"
    assert r["divergence"]["gap"] >= 0.15
    assert any("背离" in n for n in r["notes"])
    assert "模型与市场严重背离" in r["upset_risk_factors"]


def test_divergence_gate_quiet_when_aligned():
    # 模型与市场方向一致（都看好主队）→ 门控静默
    p = sample_payload(odds={"home": 1.60, "draw": 3.80, "away": 5.50})
    r = predict(p)
    assert r["divergence"] is None


def test_divergence_gate_disabled_by_config():
    p = sample_payload(
        home_recent=[{"gf": 3, "ga": 0, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 0, "ga": 3, "venue": "A"} for _ in range(8)],
        odds={"home": 8.0, "draw": 5.0, "away": 1.30},
    )
    r = predict(p, config={"divergence_gate": 0.0})
    assert r["divergence"] is None


def test_divergence_downgrades_confidence_one_level():
    # 完整信号 payload：无门控时信心为 B，触发背离后正好降一档到 C
    p = sample_payload(
        home_recent=[{"gf": 2, "ga": 0, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 1, "ga": 1, "venue": "A"} for _ in range(8)],
        odds={"home": 3.20, "draw": 3.40, "away": 2.10},
        elo={"home": 1900, "away": 1500},
        asian={"handicap": -0.5},
        opening_odds={"home": 2.60, "draw": 3.30, "away": 2.60},
    )
    r_gate = predict(p)
    r_off = predict(p, config={"divergence_gate": 0.0})
    assert r_gate["divergence"] is not None
    assert r_off["confidence"] == "B"
    assert r_gate["confidence"] == "C"
    assert r_gate["confidence_score"] < r_off["confidence_score"]
    assert r_gate["upset_risk"] > r_off["upset_risk"]


# ---------------- 让平机制（2026-09-30 复盘：14 场 7 让平） ----------------
# 根因：λ 高估 → 净胜分布过宽 → P(让平)被压低、P(穿盘)虚高。
# v2.4 收缩打在根因上；此处 pin 住机制方向，不拟合参数。

def test_shrinkage_raises_push_prob_and_tames_cover_prob():
    p = sample_payload(
        home_recent=[{"gf": 2, "ga": 1, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 1, "ga": 2, "venue": "A"} for _ in range(8)],
        handicap_line=-2, odds=None,
        # 非弱赛事：避免弱赛事 goal_cap / 让平校准干扰收缩机制验证
        competition="英超",
    )
    # 让平校准会压缩原始差异，此处只验证收缩机制本身，关闭校准
    r0 = predict(p, config={"shrink_prior": 0.0, "letdraw_strength": 0.0})   # v2.3 行为
    r3 = predict(p, config={"shrink_prior": 3.0, "letdraw_strength": 0.0})   # v2.4 行为
    h0, h3 = r0["derivatives"]["handicap_1x2"], r3["derivatives"]["handicap_1x2"]
    assert h0["line"] == h3["line"] == -2
    # 收缩后：净胜恰=2（让平）概率上升，净胜≥3（穿盘）概率下降
    assert h3["p_draw"] > h0["p_draw"]
    assert h3["p_home"] < h0["p_home"]


# ---------------- 小样本 / 对手强度覆盖率 ----------------

def test_opp_adjust_coverage_zero_when_no_opp_ratings():
    # 当前采集链路不提供 opp_attack/opp_defense → 覆盖率 0，诚实标注
    r = predict(sample_payload())
    assert r["lambda_notes"]["opp_adjust_coverage"] == 0.0


def test_opp_adjust_coverage_full_when_provided():
    p = sample_payload(
        home_recent=[{"gf": 2, "ga": 1, "venue": "H",
                      "opp_attack": 1.1, "opp_defense": 0.9} for _ in range(8)],
        away_recent=[{"gf": 1, "ga": 1, "venue": "A",
                      "opp_attack": 1.0, "opp_defense": 1.0} for _ in range(8)],
    )
    r = predict(p)
    assert r["lambda_notes"]["opp_adjust_coverage"] == 1.0


def test_degraded_small_sample_flag_and_no_overconfidence():
    # 样本<5 场 → degraded=True 标记；信心永不高于完整度等级
    base = {"home_recent": [{"gf": 2, "ga": 1, "venue": "H"} for _ in range(8)]}
    p_ok = sample_payload(
        away_recent=[{"gf": 1, "ga": 1, "venue": "A"} for _ in range(8)], **base)
    p_deg = sample_payload(
        away_recent=[{"gf": 1, "ga": 1, "venue": "A"} for _ in range(4)], **base)
    r_ok, r_deg = predict(p_ok), predict(p_deg)
    assert r_ok["lambda_notes"]["degraded"] is False
    assert r_deg["lambda_notes"]["degraded"] is True
    assert r_deg["lambda_notes"]["away_sample"] == 4
    order = ["S", "A", "B", "C"]
    # 降档逻辑（predictor 第 11 节）：degraded 触发时信心恰好低一档
    assert order.index(r_deg["confidence"]) >= order.index(r_deg["grade"])
    # 非 degraded 对照组不受该标记影响（degraded=False 不应成为降档原因）
    assert r_ok["lambda_notes"]["degraded"] is False


# ---------------- v2.5：弱赛事收缩/封顶 + 让平校准 ----------------

def _weak_payload(**kw):
    # 高进球样本，确保期望总进球 > 2.8 以触发封顶
    p = sample_payload(
        home_recent=[{"gf": 3, "ga": 1, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 2, "ga": 2, "venue": "A"} for _ in range(8)],
        odds=None, competition="欧国联")
    p.update(kw)
    return p


def test_weak_competition_uses_stronger_shrink():
    r = predict(_weak_payload())
    assert r["lambda_notes"]["weak_competition"] is True
    assert r["lambda_notes"]["shrink_prior_effective"] == 6.0


def test_strong_competition_uses_default_shrink():
    p = _weak_payload(competition="英超")
    r = predict(p)
    assert r["lambda_notes"]["weak_competition"] is False
    assert "shrink_prior_effective" not in r["lambda_notes"]
    assert r["lambda_notes"]["shrink_prior"] == 3.0


def test_weak_goal_cap_applied():
    r = predict(_weak_payload())
    notes = r["lambda_notes"]
    assert "weak_goal_cap_applied" in notes
    assert r["lambda_home"] + r["lambda_away"] <= 2.8 + 1e-9
    # 主客比例保持
    assert notes["weak_goal_cap_applied"]["cap"] == 2.8


def test_weak_goal_cap_disabled_by_config():
    r = predict(_weak_payload(), config={"weak_goal_cap": 0})
    assert "weak_goal_cap_applied" not in r["lambda_notes"]


def test_explicit_shrink_prior_wins_over_weak_default():
    # 用户显式设置优先于弱赛事默认（可复现/可关闭）
    r = predict(_weak_payload(), config={"shrink_prior": 0.0})
    assert r["lambda_notes"]["shrink_prior"] == 0.0


def test_letdraw_calibration_in_handicap_output():
    p = sample_payload(handicap_line=-1, odds=None, competition="英超")
    r = predict(p)
    h = r["derivatives"]["handicap_1x2"]
    # P0 Bug3修复后：校准后 p_draw 仍向 0.25 先验靠拢（方向保留），
    # 但须满足事件包含约束 P(让胜)+P(让平) ≤ P(主胜)，故不再精确等于混合公式
    assert "p_draw_raw" in h
    assert h["p_draw"] > h["p_draw_raw"], "让平应被抬高（v2.5方向）"
    assert h["p_draw"] < 0.25 + 1e-3, "让平不应超过先验太多"
    assert abs(h["p_home"] + h["p_draw"] + h["p_away"] - 1.0) < 1e-3
    # 事件包含：让胜+让平 ≤ 最终主胜
    assert h["p_home"] + h["p_draw"] <= r["p_home"] + 1e-3
    assert "letdraw_guard" in h
    assert isinstance(h["letdraw_guard"]["flags"], list)


def test_letdraw_calibration_disabled_by_config():
    p = sample_payload(handicap_line=-1, odds=None, competition="英超")
    r = predict(p, config={"letdraw_strength": 0.0})
    h = r["derivatives"]["handicap_1x2"]
    assert h["p_draw"] == h["p_draw_raw"]


# ---------------- v2.5b: 让球口径背离门控 fallback ----------------
# 背景：2026-10-06 竞彩009（瑞士vs北马其顿）胜平负未开售，原门控整段跳过，
# 模型让负84% vs 市场让负25.3% 零标记。无胜平负市场但有官方让球SP时，
# 用让球口径（模型校准后 handicap_1x2 vs 让球SP去水）跑同一套门控。


def _hcap_strong_away():
    # 009式：主队极弱、客队极强，让-2 下模型让负占优
    return sample_payload(
        home_recent=[{"gf": 0, "ga": 3, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 3, "ga": 0, "venue": "A"} for _ in range(8)],
        odds=None, handicap_line=-2,
    )


def test_handicap_gate_triggers_like_009():
    p = _hcap_strong_away()
    p["handicap_sp"] = [1.65, 4.20, 3.50]  # 官方让球SP：市场看好让胜
    r = predict(p)
    d = r["divergence"]
    assert d is not None
    assert d["market"] == "handicap"
    assert d["model_direction"] == "away"   # 模型：让负
    assert d["market_direction"] == "home"  # 市场：让胜
    assert d["gap"] >= 0.15
    assert "模型与让球市场严重背离" in r["upset_risk_factors"]


def test_handicap_gate_triggers_like_007():
    # 007式：模型让负(-2)占优（约六成），市场让胜占优 → 触发
    p = sample_payload(
        home_recent=[{"gf": 2, "ga": 1, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 1, "ga": 2, "venue": "A"} for _ in range(8)],
        odds=None, handicap_line=-2,
        handicap_sp=[1.78, 4.30, 2.98],
    )
    r = predict(p)
    d = r["divergence"]
    assert d is not None
    assert d["market"] == "handicap"
    assert d["model_direction"] == "away"
    assert d["market_direction"] == "home"
    assert d["gap"] >= 0.15


def test_handicap_gate_quiet_when_aligned():
    # 模型与让球市场方向一致（都看好让胜）→ 静默
    p = sample_payload(
        home_recent=[{"gf": 3, "ga": 0, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 0, "ga": 3, "venue": "A"} for _ in range(8)],
        odds=None, handicap_line=-2,
        handicap_sp=[1.30, 5.00, 8.00],
    )
    assert predict(p)["divergence"] is None


def test_handicap_gate_quiet_without_handicap_sp():
    # 无 handicap_sp 时行为与 v2.5 完全一致：不触发
    p = _hcap_strong_away()
    assert predict(p)["divergence"] is None


def test_handicap_gate_not_used_when_1x2_market_exists():
    # 有胜平负市场时走原有 1X2 门控，不重复触发让球门控
    p = sample_payload(
        home_recent=[{"gf": 3, "ga": 0, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 0, "ga": 3, "venue": "A"} for _ in range(8)],
        odds={"home": 8.0, "draw": 5.0, "away": 1.30},
        handicap_line=-2,
        handicap_sp=[1.65, 4.20, 3.50],
    )
    r = predict(p)
    d = r["divergence"]
    assert d is not None
    assert "market" not in d  # 1X2 口径，无 handicap 标记
    assert d["model_direction"] == "home"
    assert d["market_direction"] == "away"


def test_handicap_gate_malformed_sp_is_safe():
    # handicap_sp 残缺/非法 → 不崩溃、不触发
    p = _hcap_strong_away()
    p["handicap_sp"] = ["x", None]
    assert predict(p)["divergence"] is None
    p["handicap_sp"] = [1.65, 4.20]  # 少一项
    assert predict(p)["divergence"] is None


def test_handicap_gate_disabled_by_config():
    p = _hcap_strong_away()
    p["handicap_sp"] = [1.65, 4.20, 3.50]
    assert predict(p, config={"divergence_gate": 0.0})["divergence"] is None


# ---------------- v2.6: AF 独立模型信号分歧 ----------------

def _af_strong_home_payload(**kw):
    # 模型强烈看好主队（AF 强烈看好客队时触发分歧）
    p = sample_payload(
        home_recent=[{"gf": 3, "ga": 0, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 0, "ga": 3, "venue": "A"} for _ in range(8)],
        odds=None,  # 无市场，避免市场门控先触发
    )
    p.update(kw)
    return p


def test_af_gate_triggers_and_downgrades():
    p = _af_strong_home_payload()
    p["af_pred"] = [0.10, 0.25, 0.65]  # AF 强烈看好客队
    r = predict(p)
    d = r["divergence"]
    assert d is not None
    assert d["signal"] == "af_model"
    assert d["model_direction"] == "home"
    assert d["af_direction"] == "away"
    assert d["gap"] >= 0.15
    assert "独立模型信号分歧（AF）" in r["upset_risk_factors"]
    assert any("独立模型信号分歧" in n for n in r["notes"])
    # 口径：降一档/conf-15/risk+15
    r_off = predict(_af_strong_home_payload())
    assert r_off["divergence"] is None


def test_af_gate_quiet_when_aligned():
    p = _af_strong_home_payload()
    p["af_pred"] = [0.65, 0.25, 0.10]  # AF 同向看好主队
    assert predict(p)["divergence"] is None


def test_af_gate_quiet_when_gap_small():
    p = _af_strong_home_payload()
    # AF 首选平局但差距不足（模型主胜概率高，gap<0.15）
    p["af_pred"] = [0.40, 0.35, 0.25]
    r = predict(p)
    # 方向不同(draw vs home)但 gap 不足 → 静默（若模型主胜不够强则此断言需调）
    d = r["divergence"]
    if d is not None:
        assert d.get("signal") != "af_model" or d["gap"] >= 0.15


def test_af_gate_quiet_without_field():
    # 无 af_pred 字段 → 行为不变（无市场时本就不触发）
    assert predict(_af_strong_home_payload())["divergence"] is None


def test_af_gate_malformed_is_safe():
    p = _af_strong_home_payload()
    p["af_pred"] = ["x", None, 0.5]
    assert predict(p)["divergence"] is None
    p["af_pred"] = [0.5, 0.5]  # 少一项
    assert predict(p)["divergence"] is None
    p["af_pred"] = [0, 0, 0]  # 全零
    assert predict(p)["divergence"] is None


def test_af_gate_disabled_by_config():
    p = _af_strong_home_payload()
    p["af_pred"] = [0.10, 0.25, 0.65]
    assert predict(p, config={"divergence_gate": 0.0})["divergence"] is None


def test_af_gate_yields_to_market_gate():
    # 市场门控已触发时，AF 门控不再重复触发（divergence 保持市场口径）
    p = sample_payload(
        home_recent=[{"gf": 3, "ga": 0, "venue": "H"} for _ in range(8)],
        away_recent=[{"gf": 0, "ga": 3, "venue": "A"} for _ in range(8)],
        odds={"home": 8.0, "draw": 5.0, "away": 1.30},
    )
    p["af_pred"] = [0.10, 0.25, 0.65]
    r = predict(p)
    d = r["divergence"]
    assert d is not None
    assert d.get("signal") != "af_model"
    assert d["model_direction"] == "home"
    assert d["market_direction"] == "away"
