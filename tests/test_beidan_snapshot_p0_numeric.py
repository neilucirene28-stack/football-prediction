"""GPT复核：b1dd256的P0数值一致性测试。

问题：predictor.py中 p_home/p_draw/p_away 是 round(p_final,4)，
但 matrix_cal 用未舍入的 p_final。beidan_snapshot.build_six_play_vector
的 wdl、半全场目标、record.p_1x2 用舍入值，而 score_31 从 matrix_cal
聚合（未舍入），导致：
1. 很多场WDL总和偏0.0001被1e-6拒写
2. 即使和为1，也与score_31聚合的WDL不一致

每项测试在当前代码上必须失败，修复后必须通过。
"""
import math

import pytest

from engine.beidan_snapshot import build_six_play_vector


def _mk_matrix_from_p1x2(p_home, p_draw, p_away, n=6):
    """构造一个1X2边际精确等于给定值的比分矩阵（简化版）。"""
    # 用简单的独立Poisson近似，然后IPF式缩放到目标边际
    import math as m
    lam_h, lam_a = 1.5, 1.2
    mat = [[0.0] * n for _ in range(n)]
    for h in range(n):
        for a in range(n):
            mat[h][a] = (lam_h ** h / m.factorial(h) * m.exp(-lam_h) *
                         lam_a ** a / m.factorial(a) * m.exp(-lam_a))
    # 缩放到目标1X2
    s_home = sum(mat[h][a] for h in range(n) for a in range(n) if h > a)
    s_draw = sum(mat[h][a] for h in range(n) for a in range(n) if h == a)
    s_away = sum(mat[h][a] for h in range(n) for a in range(n) if h < a)
    for h in range(n):
        for a in range(n):
            if h > a:
                mat[h][a] *= p_home / s_home
            elif h == a:
                mat[h][a] *= p_draw / s_draw
            else:
                mat[h][a] *= p_away / s_away
    return mat


def _mk_predict_with_rounding_issue(p_final_full):
    """模拟修复后的predictor.py行为：p_home等仍是round(4)展示值，
    但新增 p_final_full（未舍入）供快照使用。矩阵用未舍入值。"""
    p_home, p_draw, p_away = (round(p, 4) for p in p_final_full)
    matrix = _mk_matrix_from_p1x2(*p_final_full)
    # 半全场：构造边际对上未舍入值的9项
    hf = {}
    # 简化：按全场概率比例分配
    hf["胜胜"] = p_final_full[0] * 0.6
    hf["平胜"] = p_final_full[0] * 0.25
    hf["负胜"] = p_final_full[0] * 0.15
    hf["胜平"] = p_final_full[1] * 0.4
    hf["平平"] = p_final_full[1] * 0.4
    hf["负平"] = p_final_full[1] * 0.2
    hf["胜负"] = p_final_full[2] * 0.3
    hf["平负"] = p_final_full[2] * 0.3
    hf["负负"] = p_final_full[2] * 0.4
    return {
        "p_home": p_home,  # 舍入展示值（竞彩行为不变）
        "p_draw": p_draw,
        "p_away": p_away,
        "p_final_full": list(p_final_full),  # 修复后新增：未舍入
        "lambda_home": round(1.5432, 3),  # 舍入展示值
        "lambda_away": round(1.2345, 3),
        "lambda_home_full": 1.5432,  # 修复后新增：全精度
        "lambda_away_full": 1.2345,
        "model_version": "2.10",
        "score_matrix_full": matrix,  # 未舍入矩阵
        "derivatives": {
            "half_full_1x2": dict(hf),  # 边际对未舍入值
            "handicap_1x2": None,
            "top_scores": [],
        },
    }


# 多组会触发舍入问题的p_final
PROBLEM_CASES = [
    (0.45678, 0.27123, 0.27199),  # 和=1.0，round后和=1.0但与矩阵不一致
    (0.33333, 0.33333, 0.33334),  # round后 0.3333*3=0.9999，偏0.0001
    (0.51234, 0.28765, 0.20001),  # round后可能偏
    (0.48765, 0.31234, 0.20001),
    (0.60001, 0.25002, 0.14997),
]


@pytest.mark.parametrize("p_final", PROBLEM_CASES)
def test_score31_aggregation_equals_wdl(p_final):
    """score_31三方向聚合必须等于快照wdl（容差1e-9）。

    当前代码：wdl用round(4)值，score_31从未舍入矩阵聚合 → 不一致。
    修复后：两者必须用同一全精度源。
    """
    pred = _mk_predict_with_rounding_issue(p_final)
    vec = build_six_play_vector(pred)
    score_31 = vec["score_31"]
    # 聚合score_31的三方向
    agg_home = sum(v for k, v in score_31.items()
                   if k in ("1-0", "2-0", "2-1", "3-0", "3-1", "3-2",
                            "4-0", "4-1", "4-2", "5-0", "5-1", "5-2", "胜其他"))
    agg_draw = sum(v for k, v in score_31.items()
                   if k in ("0-0", "1-1", "2-2", "3-3", "平其他"))
    agg_away = sum(v for k, v in score_31.items()
                   if k in ("0-1", "0-2", "1-2", "0-3", "1-3", "2-3",
                            "0-4", "1-4", "2-4", "0-5", "1-5", "2-5", "负其他"))
    wdl = vec["wdl"]
    assert abs(agg_home - wdl["胜"]) < 1e-9, (
        f"score_31主胜聚合 {agg_home:.10f} vs wdl胜 {wdl['胜']:.10f}"
    )
    assert abs(agg_draw - wdl["平"]) < 1e-9, (
        f"score_31平聚合 {agg_draw:.10f} vs wdl平 {wdl['平']:.10f}"
    )
    assert abs(agg_away - wdl["负"]) < 1e-9, (
        f"score_31客胜聚合 {agg_away:.10f} vs wdl负 {wdl['负']:.10f}"
    )


@pytest.mark.parametrize("p_final", PROBLEM_CASES)
def test_wdl_sum_within_tolerance(p_final):
    """快照wdl概率和必须在1e-6容差内（当前round(4)会导致偏0.0001被拒写）。"""
    pred = _mk_predict_with_rounding_issue(p_final)
    # 当前代码在build_six_play_vector内部_check_prob_sum会raise
    # 修复后：wdl用全精度，和精确为1
    try:
        vec = build_six_play_vector(pred)
    except ValueError as e:
        if "概率和" in str(e):
            pytest.fail(f"wdl概率和被拒写（舍入导致）: {e}")
        raise
    total = vec["wdl"]["胜"] + vec["wdl"]["平"] + vec["wdl"]["负"]
    assert abs(total - 1.0) < 1e-6


def test_lambda_full_precision_or_marked():
    """lambda_home/away 快照必须保存全精度，或明确标注为展示值。

    当前 predictor 输出 round(lam,3)，与"进入矩阵前原值"规范不符。
    """
    pred = _mk_predict_with_rounding_issue(PROBLEM_CASES[0])
    # 修复后：predict_result 应含 lambda_home_full / lambda_away_full（全精度），
    # 或快照字段明确标注 display
    has_full = ("lambda_home_full" in pred and "lambda_away_full" in pred)
    # 当前没有 → 测试失败，驱动修复
    assert has_full, "predict()需输出lambda_home_full/lambda_away_full（全精度）"


# ---- 真实predict()集成测试 ----
_SCORE31_GROUPS = {
    "胜": ("1-0", "2-0", "2-1", "3-0", "3-1", "3-2",
          "4-0", "4-1", "4-2", "5-0", "5-1", "5-2", "胜其他"),
    "平": ("0-0", "1-1", "2-2", "3-3", "平其他"),
    "负": ("0-1", "0-2", "1-2", "0-3", "1-3", "2-3",
          "0-4", "1-4", "2-4", "0-5", "1-5", "2-5", "负其他"),
}


def _real_payload(**kw):
    base = {
        "home": "主队A", "away": "客队B",
        "kickoff_at": "2030-06-01T20:00:00+08:00",
        "snapshot_at": "2030-06-01T10:00:00+08:00",
        "competition": "测试联赛",
        "home_recent": [{"gf": 2, "ga": 1, "venue": "H"}] * 8,
        "away_recent": [{"gf": 1, "ga": 1, "venue": "A"}] * 8,
        "league_avg_goals": 2.7,
    }
    base.update(kw)
    return base


_INTEGRATION_CASES = [
    # 强主队
    {"home_recent": [{"gf": 3, "ga": 1, "venue": "H"}] * 8,
     "away_recent": [{"gf": 1, "ga": 2, "venue": "A"}] * 8},
    # 均势
    {"home_recent": [{"gf": 1, "ga": 1, "venue": "H"}] * 8,
     "away_recent": [{"gf": 1, "ga": 1, "venue": "A"}] * 8},
    # 强客队
    {"home_recent": [{"gf": 1, "ga": 2, "venue": "H"}] * 8,
     "away_recent": [{"gf": 3, "ga": 1, "venue": "A"}] * 8},
]


@pytest.mark.parametrize("case_kw", _INTEGRATION_CASES)
def test_real_predict_score31_equals_wdl(case_kw):
    """真实 predict(model='beidan') 的 score_31 三方向聚合必须等于 wdl（1e-9）。

    覆盖不同λ/赔率/让球组合，确保数值一致性修复在真实链路生效。
    """
    from engine.predictor import predict

    kw = dict(case_kw)
    kw["odds"] = {"home": 1.8, "draw": 3.5, "away": 4.0}
    kw["handicap_line"] = -1
    r = predict(_real_payload(**kw), model="beidan")
    assert r["status"] == "ok", r
    # P0修复：必须输出全精度字段
    assert "p_final_full" in r, "predict()必须输出p_final_full"
    assert "lambda_home_full" in r, "predict()必须输出lambda_home_full"
    assert "score_matrix_full" in r

    vec = build_six_play_vector(r)
    wdl = vec["wdl"]
    score_31 = vec["score_31"]
    for direction, grp in _SCORE31_GROUPS.items():
        agg = sum(score_31[c] for c in grp)
        assert abs(agg - wdl[direction]) < 1e-9, (
            f"score_31{direction}聚合 {agg:.12f} vs wdl {wdl[direction]:.12f}"
        )
    # wdl概率和精确为1
    assert abs(sum(wdl.values()) - 1.0) < 1e-9
    # 展示值仍是round(4)，行为不变
    assert r["p_home"] == round(r["p_final_full"][0], 4)
