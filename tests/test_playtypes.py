"""竞彩官方五大玩法输出格式测试（2026-10-06 定版）。

覆盖：
- half_full_1x2：9 种组合概率加总=1；半场负全场胜概率>0（手动验算逻辑）；
  联合分布推导的边际 ≈ 独立边际（半场/全场）。
- total_goals_exact：0~7+ 概率加总=1；"7+" 归尾正确。
- predict() 输出含新衍生键且加总=1。
- scripts/jingcai_format.py 五个格式化函数可用。
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.poisson import (
    score_matrix, match_probs, half_time_probs,
    half_full_1x2, total_goals_exact,
)
from engine.predictor import predict

HALF_FULL_KEYS = ("胜胜", "胜平", "胜负",
                  "平胜", "平平", "平负",
                  "负胜", "负平", "负负")


def test_half_full_sums_to_one():
    for lam_h, lam_a in [(1.8, 1.2), (0.6, 0.6), (3.2, 0.5), (1.0, 2.5),
                         (0.3, 0.3), (2.0, 2.0)]:
        d = half_full_1x2(lam_h, lam_a)
        assert set(d.keys()) == set(HALF_FULL_KEYS), d.keys()
        assert abs(sum(d.values()) - 1.0) < 1e-6, (lam_h, lam_a, sum(d.values()))
        assert all(v >= 0.0 for v in d.values())


def test_half_full_away_win_comeback_positive():
    # 手动验算：强主队半场落后、全场逆转的概率必须 > 0
    # （半场 0-1 且下半场主队进 2+ 球的路径存在）
    d = half_full_1x2(2.0, 0.8)
    assert d["负胜"] > 0.0
    assert d["负胜"] < d["胜胜"]  # 逆转比一路领先少见
    # 弱主队被逆转的概率也应 > 0
    d2 = half_full_1x2(0.8, 2.0)
    assert d2["胜负"] > 0.0


def test_half_full_marginals_consistent():
    # 联合分布推导的全场边际 ≈ 直接矩阵的全场 1X2
    # 联合分布推导的半场边际 ≈ half_time_probs
    # 注：Dixon-Coles 修正施加在半场比分矩阵上，下半场独立进球会稀释
    # 其对全场平局的提升，因此联合推导的 P(平) 略低于直接全场矩阵
    # （如 λ=1.0/1.0 时 0.309 vs 0.344），属方法差异非 bug，容差放宽到 0.05。
    for lam_h, lam_a in [(1.8, 1.2), (1.0, 1.0), (2.5, 0.7)]:
        d = half_full_1x2(lam_h, lam_a)
        ft_home = sum(v for k, v in d.items() if k[1] == "胜")
        ft_draw = sum(v for k, v in d.items() if k[1] == "平")
        ft_away = sum(v for k, v in d.items() if k[1] == "负")
        ph, pd, pa = match_probs(score_matrix(lam_h, lam_a, rho=-0.13))
        assert abs(ft_home - ph) < 0.03, (lam_h, lam_a, ft_home, ph)
        assert abs(ft_draw - pd) < 0.05, (lam_h, lam_a, ft_draw, pd)
        assert abs(ft_away - pa) < 0.03, (lam_h, lam_a, ft_away, pa)
        ht_home = sum(v for k, v in d.items() if k[0] == "胜")
        ht_draw = sum(v for k, v in d.items() if k[0] == "平")
        ht_away = sum(v for k, v in d.items() if k[0] == "负")
        ht = half_time_probs(lam_h, lam_a)
        assert abs(ht_home - ht["p_home"]) < 0.02
        assert abs(ht_draw - ht["p_draw"]) < 0.02
        assert abs(ht_away - ht["p_away"]) < 0.02


def test_half_full_not_independent_product():
    # 半场与全场不独立：联合概率 ≠ 边际乘积（至少有一个组合显著偏离）
    d = half_full_1x2(1.8, 1.2)
    ht = half_time_probs(1.8, 1.2)
    ph, pd, pa = match_probs(score_matrix(1.8, 1.2, rho=-0.13))
    # 若独立，P(胜胜) = P(HT胜)×P(FT胜)；实际应显著不同（半场领先者更可能全场胜）
    indep = ht["p_home"] * ph
    assert abs(d["胜胜"] - indep) > 0.01, (d["胜胜"], indep)


def test_total_goals_exact_sums_to_one():
    for lam_h, lam_a in [(1.8, 1.2), (0.5, 0.5), (3.0, 1.5), (0.2, 0.2)]:
        m = score_matrix(lam_h, lam_a, rho=-0.13)
        d = total_goals_exact(m)
        expected_keys = {0, 1, 2, 3, 4, 5, 6, "7+"}
        assert set(d.keys()) == expected_keys, d.keys()
        assert abs(sum(d.values()) - 1.0) < 1e-9
        assert all(v >= 0.0 for v in d.values())


def test_total_goals_exact_tail_matches_manual():
    # 手动验算归尾：P(7+) = 1 - P(0..6)
    m = score_matrix(2.2, 1.4, rho=-0.13)
    d = total_goals_exact(m)
    manual_7plus = sum(m[i][j] for i in range(len(m)) for j in range(len(m))
                       if i + j >= 7)
    assert abs(d["7+"] - manual_7plus) < 1e-12
    # 低进球分布（总期望<1）下 7+ 应很小，且单调递减
    m2 = score_matrix(0.3, 0.3, rho=-0.13)
    d2 = total_goals_exact(m2)
    assert d2["7+"] < 0.01
    assert d2[0] > d2[1] > d2[2]  # 总期望 0.6 <1 时单调递减


def _sample_payload(**kw):
    def mk(gf, ga, venue):
        return [{"gf": gf, "ga": ga, "venue": venue} for _ in range(8)]

    now = datetime.now().astimezone()
    p = {
        "home": "法国", "away": "比利时", "competition": "欧国联",
        "kickoff_at": (now + timedelta(days=2)).isoformat(),
        "snapshot_at": now.isoformat(),
        "league_avg_goals": 2.70,
        "home_recent": mk(2, 1, "H"),
        "away_recent": mk(1, 1, "A"),
        "odds": {"home": 1.48, "draw": 4.70, "away": 5.90},
        "handicap_line": -1,
        "ou_line": 2.5,
    }
    p.update(kw)
    return p


def test_predictor_has_new_derivs():
    r = predict(_sample_payload())
    assert r["status"] == "ok"
    d = r["derivatives"]
    assert "half_full_1x2" in d and "total_goals_exact" in d
    hf = d["half_full_1x2"]
    assert set(hf.keys()) == set(HALF_FULL_KEYS)
    # 4 位小数舍入容差
    assert abs(sum(hf.values()) - 1.0) < 5e-4
    tg = d["total_goals_exact"]
    assert set(tg.keys()) == {"0", "1", "2", "3", "4", "5", "6", "7+"}
    assert abs(sum(tg.values()) - 1.0) < 5e-4
    # 旧键不受影响
    assert "half_time" in d and "total_goals" in d


def test_format_five_playtypes():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from jingcai_format import format_five_playtypes
    r = predict(_sample_payload())
    lines = format_five_playtypes(r)
    assert len(lines) == 7  # 五大玩法 + 进球区间 + 冷门比分
    assert lines[0].startswith("胜平负：")
    assert lines[1].startswith("让球胜平负")
    assert lines[2].startswith("比分：")
    # 比分首选加粗
    assert lines[2].split("、")[0].startswith("比分：**")
    # 进球数：保持"期望+最可能区间"格式
    assert lines[3].startswith("进球数：")
    assert "期望" in lines[3] and "球(" in lines[3]
    # v2.10：进球区间行（三档分布呈现）
    assert lines[4].startswith("进球区间：")
    assert "0-1球(" in lines[4] and "2-3球(" in lines[4] and "4+球(" in lines[4]
    assert lines[5].startswith("半全场胜平负：")
    assert lines[5].count("、") == 2
    assert lines[6].startswith("冷门比分：")
    # 半全场行应含 3 种组合
    assert lines[5].count("、") == 2


def test_format_h2h_brief_no_scores():
    # 2026-10-06 09:40 精简：H2H 依据行只留方向一句话，不带任何比分数字
    import re
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from jingcai_format import format_h2h_brief
    score_pat = re.compile(r"\d+\s*[:：\-－]\s*\d+")
    cases = [
        (("法国", "比利时", 4, 0, 0), "H2H（近4场）：法国占优"),
        (("罗马尼亚", "瑞典", 1, 0, 3), "H2H（近4场）：瑞典占优"),
        (("塞浦路斯", "拉脱维亚", 0, 2, 0), "H2H（近2场）：均势"),
        (("黑山", "亚美尼亚", 0, 0, 0), "H2H：无交锋记录"),
        (("波黑", "波兰", 0, 1, 2), "H2H（近3场）：波兰占优"),
    ]
    for args, expected in cases:
        out = format_h2h_brief(*args)
        assert out == expected, (args, out)
        assert not score_pat.search(out), f"输出含比分数字: {out}"
