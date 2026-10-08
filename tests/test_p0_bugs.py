"""P0级bug回归测试（2026-10-09）。每个bug至少1个测试，修复后必须全过。"""
import sys
sys.path.insert(0, '/home/hatch/workspace/football-prediction-v2')


class TestBug1VenueMissing:
    """Bug1: venue缺失的战绩不应被静默丢弃；sample计数须与实际使用一致。"""

    def test_no_venue_records_participate(self):
        from engine.strengths import estimate_lambdas
        rec = [{"gf": 3, "ga": 2} for _ in range(8)]  # 无venue字段
        lam_h, lam_a, notes = estimate_lambdas(rec, rec, league_avg_goals=2.70)
        # 评级必须正常计算，不再退回1.0
        assert notes["attack_home"] != 1.0, "attack_home退回1.0，战绩被丢弃"
        assert notes["attack_home"] > 1.5  # 3球/场 vs 联赛均值1.35，进攻应显著>1
        assert notes["home_sample"] == 8
        assert notes["degraded"] is False

    def test_true_small_sample_still_degraded(self):
        from engine.strengths import estimate_lambdas
        rec = [{"gf": 3, "ga": 2} for _ in range(3)]
        _, _, notes = estimate_lambdas(rec, rec, league_avg_goals=2.70)
        assert notes["home_sample"] == 3
        assert notes["degraded"] is True, "真正样本不足时必须标记degraded"

    def test_explicit_venue_still_filtered(self):
        # 显式venue的主客场筛选行为不变
        from engine.strengths import attack_defense
        recs = [{"gf": 3, "ga": 0, "venue": "H"} for _ in range(4)] + \
               [{"gf": 0, "ga": 3, "venue": "A"} for _ in range(4)]
        a_h, _, d_h = attack_defense(recs, 2.70, venue="H")
        # 主场筛选应只用4场H记录（+0场N），进攻评级应高
        assert d_h["n"] == 4
        assert a_h > 1.5


class TestBug2Shin:
    """Bug2: shin_probs 不得退化为等比归一。"""

    def test_shin_differs_from_proportional(self):
        from engine.market import shin_probs, implied_proportional
        out = shin_probs((2, 3, 4))
        exp = implied_proportional((2, 3, 4))
        assert abs(sum(out) - 1.0) < 1e-9
        # 必须有可观测差异（>0.5pp）
        assert any(abs(a - b) > 0.005 for a, b in zip(out, exp)), \
            f"shin退化为等比归一: {out}"
        # Shin方向：热门真实概率应高于盘口暗示（水位加在热门身上）
        assert out[0] > exp[0]

    def test_shin_fair_book_degrades(self):
        from engine.market import shin_probs, implied_proportional
        out = shin_probs((2, 3, 6))  # booksum=1.0，无水位
        exp = implied_proportional((2, 3, 6))
        assert all(abs(a - b) < 1e-6 for a, b in zip(out, exp))


class TestBug3LetdrawInclusion:
    """Bug3: 让平校准不得违反 P(让胜)+P(让平) ≤ P(主胜)。"""

    def test_extreme_weak_home_respects_inclusion(self):
        from engine.poisson import score_matrix, match_probs, handicap_1x2
        from engine.letdraw import calibrate_handicap_1x2
        mx = score_matrix(0.35, 2.4, rho=-0.13)
        ph, _, pa = match_probs(mx)
        rh, rd, ra = handicap_1x2(mx, -1)
        ch, cd, ca = calibrate_handicap_1x2(rh, rd, ra, -1, strength=0.5,
                                            p_home=ph, p_away=pa)
        assert ch + cd <= ph + 1e-9, f"违反包含: {ch+cd:.4%} > {ph:.4%}"
        assert abs(ch + cd + ca - 1.0) < 1e-9
        assert ch >= 0 and cd >= 0 and ca >= 0

    def test_normal_case_keeps_letdraw_effect(self):
        from engine.poisson import score_matrix, match_probs, handicap_1x2
        from engine.letdraw import calibrate_handicap_1x2
        mx = score_matrix(1.8, 1.1, rho=-0.13)
        ph, _, pa = match_probs(mx)
        rh, rd, ra = handicap_1x2(mx, -1)
        ch, cd, ca = calibrate_handicap_1x2(rh, rd, ra, -1, strength=0.5,
                                            p_home=ph, p_away=pa)
        assert cd > rd, "正常场次让平应被抬高（v2.5效果）"
        assert ch + cd <= ph + 1e-9

    def test_positive_handicap_uses_away(self):
        from engine.letdraw import calibrate_handicap_1x2
        # rq=+1极端：客胜仅5%，让负+让平不得超5%
        ch, cd, ca = calibrate_handicap_1x2(0.90, 0.05, 0.05, 1, strength=0.5,
                                            p_home=0.05, p_away=0.05)
        assert ca + cd <= 0.05 + 1e-9
