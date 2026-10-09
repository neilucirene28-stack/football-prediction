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

    def test_shin_matches_reference_with_booksum(self):
        # GPT BD-1.0审计（v2.9）：根式内 qi² 必须除以 booksum B。
        # 对照 mberk/shin 参考实现：赔率(2,3,4) → (0.469414, 0.306069, 0.224517)
        from engine.market import shin_probs
        out = shin_probs((2, 3, 4))
        expected = (0.469414, 0.306069, 0.224517)
        for a, b in zip(out, expected):
            assert abs(a - b) < 1e-5, f"Shin输出{a:.6f}偏离参考值{b:.6f}"


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


class TestBug2bLetdrawMatrixEquality:
    """GPT BD-1.0审计（v2.9）：让平修正必须作用于矩阵，保证严格等式。

    rq=-1时 P(让胜)+P(让平) ≡ P(主胜)；rq=+1时 P(让负)+P(让平) ≡ P(客胜)。
    """

    def test_matrix_letdraw_exact_equality_rq_minus1(self):
        # GPT验收case：λ=(1.3,0.2)、ρ=-0.13，曾差1.85pp
        from engine.poisson import score_matrix, match_probs, handicap_1x2
        from engine.letdraw import apply_letdraw_to_matrix
        mx = score_matrix(1.3, 0.2, rho=-0.13)
        ph, _, _ = match_probs(mx)
        mx_ld = apply_letdraw_to_matrix(mx, -1, strength=0.5)
        h2, d2, a2 = handicap_1x2(mx_ld, -1)
        assert abs((h2 + d2) - ph) < 1e-8, f"差{abs(h2+d2-ph):.2e}超1e-8"
        assert abs(h2 + d2 + a2 - 1.0) < 1e-9

    def test_matrix_letdraw_exact_equality_rq_plus1(self):
        from engine.poisson import score_matrix, match_probs, handicap_1x2
        from engine.letdraw import apply_letdraw_to_matrix
        mx = score_matrix(0.4, 1.9, rho=-0.13)
        _, _, pa = match_probs(mx)
        mx_ld = apply_letdraw_to_matrix(mx, 1, strength=0.5)
        h2, d2, a2 = handicap_1x2(mx_ld, 1)
        assert abs((a2 + d2) - pa) < 1e-8

    def test_matrix_letdraw_preserves_v25_effect(self):
        # v2.5语义保留：边际P(让平)向先验收缩
        from engine.poisson import score_matrix, handicap_1x2
        from engine.letdraw import apply_letdraw_to_matrix, letdraw_prior
        mx = score_matrix(1.3, 0.2, rho=-0.13)
        _, d_raw, _ = handicap_1x2(mx, -1)
        mx_ld = apply_letdraw_to_matrix(mx, -1, strength=0.5)
        _, d2, _ = handicap_1x2(mx_ld, -1)
        prior = letdraw_prior(-1)
        # 修正值应在raw与先验之间（本例raw>prior，故下降）
        assert min(d_raw, prior) - 1e-9 <= d2 <= max(d_raw, prior) + 1e-9
        # 与旧事后混合的边际值一致（0.5*0.3219+0.5*0.25）
        assert abs(d2 - (0.5 * d_raw + 0.5 * prior)) < 1e-9

    def test_matrix_letdraw_preserves_region_mass(self):
        from engine.poisson import score_matrix
        from engine.letdraw import apply_letdraw_to_matrix
        mx = score_matrix(1.8, 1.1, rho=-0.13)
        mx_ld = apply_letdraw_to_matrix(mx, -1, strength=0.5)
        n = len(mx)
        pw0 = sum(mx[i][j] for i in range(n) for j in range(n) if i > j)
        pw1 = sum(mx_ld[i][j] for i in range(n) for j in range(n) if i > j)
        assert abs(pw0 - pw1) < 1e-12

    def test_matrix_letdraw_strength_zero_is_identity(self):
        from engine.poisson import score_matrix
        from engine.letdraw import apply_letdraw_to_matrix
        mx = score_matrix(1.8, 1.1, rho=-0.13)
        mx_ld = apply_letdraw_to_matrix(mx, -1, strength=0.0)
        assert all(abs(mx_ld[i][j] - mx[i][j]) < 1e-12
                   for i in range(len(mx)) for j in range(len(mx)))

    def test_predict_end_to_end_handicap_equality(self):
        from datetime import datetime, timedelta, timezone
        from engine.predictor import predict
        now = datetime.now(timezone.utc)
        res = predict({
            "home": "测试主", "away": "测试客",
            "kickoff_at": (now + timedelta(hours=5)).isoformat(),
            "snapshot_at": now.isoformat(),
            "home_recent": [{"gf": 2, "ga": 1, "venue": "H"} for _ in range(8)],
            "away_recent": [{"gf": 1, "ga": 1, "venue": "A"} for _ in range(8)],
            "league_avg_goals": 2.70,
            "handicap_line": -1,
        })
        hc = res["derivatives"]["handicap_1x2"]
        # 4位round引入≤1e-4误差
        assert abs((hc["p_home"] + hc["p_draw"]) - res["p_home"]) < 5e-4


class TestBug4HalfFullMarginal:
    """Bug4: 半全场9格聚合的全场边际必须等于最终胜平负（<0.5pp）。"""

    def _payload(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        return {
            "home": "测试主", "away": "测试客",
            "kickoff_at": (now + timedelta(hours=5)).isoformat(),
            "snapshot_at": now.isoformat(),
            "home_recent": [{"gf": 2, "ga": 1, "venue": "H"} for _ in range(8)],
            "away_recent": [{"gf": 1, "ga": 1, "venue": "A"} for _ in range(8)],
            "league_avg_goals": 2.70,
            "odds": {"home": 2.0, "draw": 3.4, "away": 3.6},
            "handicap_line": -1,
        }

    def test_half_full_marginal_matches_final(self):
        from engine.predictor import predict
        res = predict(self._payload())
        hf = res["derivatives"]["half_full_1x2"]
        agg = [sum(v for k, v in hf.items() if k[1] == o) for o in ("胜", "平", "负")]
        final = (res["p_home"], res["p_draw"], res["p_away"])
        diff = max(abs(a - f) for a, f in zip(agg, final))
        assert diff < 0.005, f"边际差{diff:.4%}超0.5pp"
        assert abs(sum(hf.values()) - 1.0) < 0.01

    def test_half_full_without_ft_matrix_keeps_old_behavior(self):
        # ft_matrix=None 时保持原行为（向后兼容）
        from engine.poisson import half_full_1x2
        out = half_full_1x2(1.8, 1.1)
        assert abs(sum(out.values()) - 1.0) < 1e-6
        assert set(out.keys()) == {"胜胜", "胜平", "胜负", "平胜", "平平",
                                    "平负", "负胜", "负平", "负负"}
