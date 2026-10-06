"""北单适配层测试：校准隔离性、冷门分层、adjusted goals。"""
import math

import pytest

from engine.beidan_calibration import (
    apply_beidan_calibration, get_beidan_params, beidan_calibration_info,
)
from engine.beidan_upset import (
    upset_risk, risk_tier, should_exclude, WEAK_LEAGUES,
)
from engine.beidan_adjusted_goals import (
    goal_weight, adjust_goals, adjust_match, ENABLED,
)


class TestBeidanCalibration:
    def test_params_exist(self):
        # 北单专属参数已训练：sp/rq/goals
        for pt in ("sp", "rq", "goals"):
            ab = get_beidan_params(pt)
            assert ab is not None, f"缺少 {pt} 校准参数"
            a, b = ab
            assert math.isfinite(a) and math.isfinite(b)

    def test_isolated_from_jingcai(self):
        # 北单校准文件与竞彩 engine/calibration.json 是不同文件
        import os
        beidan_path = os.path.join(
            os.path.dirname(__file__), "..", "engine", "beidan_calibration.json")
        jingcai_path = os.path.join(
            os.path.dirname(__file__), "..", "engine", "calibration.json")
        assert os.path.abspath(beidan_path) != os.path.abspath(jingcai_path)
        assert os.path.exists(beidan_path)

    def test_calibration_corrects_overconfidence(self):
        # 让球模型过度自信（0.52->实际0.29），校准后应下调
        p = 0.52
        cal = apply_beidan_calibration("rq", p)
        assert cal < p, "让球校准应下调过度自信的概率"
        assert 0 < cal < 1

    def test_unknown_playtype_passthrough(self):
        # 无参数的玩法原样返回，不编造
        assert apply_beidan_calibration("hf", 0.3) == 0.3
        assert apply_beidan_calibration("dxds", 0.25) == 0.25

    def test_info(self):
        info = beidan_calibration_info()
        assert info["version"] == "beidan-v1"
        assert "26101" in info["trained_on"]


class TestBeidanUpset:
    def test_output_range(self):
        for p in (0.1, 0.5, 0.9):
            r = upset_risk(p_model_top=p, handicap=0, league="英超")
            assert 0 <= r <= 1, f"风险分超出[0,1]: {r}"

    def test_higher_confidence_higher_risk(self):
        # 实证发现（26101期）：模型越自信翻车率越高（过度自信）
        r_low = upset_risk(p_model_top=0.35, handicap=0, league="英超")
        r_high = upset_risk(p_model_top=0.80, handicap=0, league="英超")
        assert r_high > r_low

    def test_handicap_increases_risk(self):
        r0 = upset_risk(p_model_top=0.6, handicap=0, league="英超")
        r2 = upset_risk(p_model_top=0.6, handicap=-2, league="英超")
        assert r2 > r0

    def test_weak_league_effect(self):
        # 实证：弱联赛系数为负（模型在弱联赛更保守），此处仅验证方向一致
        r_strong = upset_risk(p_model_top=0.6, handicap=0, league="英超")
        r_weak = upset_risk(p_model_top=0.6, handicap=0, league="巴西乙")
        assert "巴西乙" in WEAK_LEAGUES
        # 按拟合权重，弱联赛风险略低
        assert r_weak < r_strong

    def test_tier(self):
        assert risk_tier(0.4) == "低"
        assert risk_tier(0.57) == "中"
        assert risk_tier(0.7) == "高"

    def test_should_exclude(self):
        assert should_exclude(0.7) is True
        assert should_exclude(0.5) is False


class TestAdjustedGoals:
    def test_no_discount_before_70(self):
        assert goal_weight(65, scorer_leading=True) == 1.0
        assert goal_weight(70, scorer_leading=True) == 1.0

    def test_linear_discount_after_70(self):
        # 70'->1.0, 90'->0.5 线性
        assert goal_weight(90, scorer_leading=True) == 0.5
        w80 = goal_weight(80, scorer_leading=True)
        assert 0.5 < w80 < 1.0
        # 线性验证：80' 应为 0.75
        assert abs(w80 - 0.75) < 1e-9

    def test_trailing_not_discounted(self):
        # 落后方进球不贬值
        assert goal_weight(85, scorer_leading=False) == 1.0
        assert goal_weight(90, scorer_leading=False) == 1.0

    def test_adjust_goals_simple(self):
        # 主队 1-0，75' 领先后进球应贬值
        # 回放：75' 时比分 0-0（假设这是第一个进球），不领先，不贬值
        h, a = adjust_goals([75], [])
        assert h == 1.0 and a == 0.0

    def test_adjust_goals_leading_discount(self):
        # 主队 10' 进球，80' 再进（领先时）-> 第二球贬值
        h, a = adjust_goals([10, 80], [])
        assert h == 1.0 + 0.75
        assert a == 0.0

    def test_adjust_match_no_minutes_passthrough(self):
        # 无分钟数据原样返回，不编造
        assert adjust_match(2, 1) == (2.0, 1.0)
        assert adjust_match(2, 1, None, None) == (2.0, 1.0)

    def test_adjust_match_mismatch_passthrough(self):
        # 分钟数与比分对不上，拒绝贬值
        assert adjust_match(2, 1, [10], [20]) == (2.0, 1.0)

    def test_disabled_by_default(self):
        # 默认关闭，未验证不进生产
        assert ENABLED is False


class TestPredictModelParam:
    def _payload(self):
        return {
            "home": "测试主队", "away": "测试客队",
            "kickoff_at": "2026-10-07T12:00:00+08:00",
            "snapshot_at": "2026-10-06T12:00:00+08:00",
            "competition": "测试联赛",
            "odds": {"home": 2.0, "draw": 3.2, "away": 3.5},
            "home_recent": [
                {"gf": 2, "ga": 1, "venue": "home", "date": "2026-10-01"},
            ] * 6,
            "away_recent": [
                {"gf": 1, "ga": 1, "venue": "away", "date": "2026-10-01"},
            ] * 6,
        }

    def test_default_jingcai(self):
        from engine import predict
        r = predict(self._payload())
        assert r["model"] == "jingcai"
        assert r["beidan"] == {}

    def test_beidan_model(self):
        from engine import predict
        r = predict(self._payload(), model="beidan")
        assert r["model"] == "beidan"
        b = r["beidan"]
        assert "upset_risk" in b
        assert 0 <= b["upset_risk"] <= 1
        assert b["upset_risk_tier"] in ("低", "中", "高")
        assert b["playtypes"] == ["胜平负", "让球胜平负", "比分", "总进球", "半全场", "上下单双"]
        assert "calibrated_p_top_sp" in b

    def test_invalid_model(self):
        from engine import predict
        from engine.predictor import PredictError
        with pytest.raises(PredictError):
            predict(self._payload(), model="unknown")
