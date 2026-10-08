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
