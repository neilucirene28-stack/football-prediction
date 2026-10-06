"""让平单独建模（v2.5）测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.letdraw import (
    TIER_PRIOR, LEAGUE_PRIOR_1,
    letdraw_prior, calibrate_handicap_1x2, letdraw_guard,
)


def test_tier_prior_values_match_empirical():
    # 418 场回填经验值：|rq|=1 → 0.25，|rq|=2 → 0.21
    assert TIER_PRIOR[1] == 0.25
    assert TIER_PRIOR[2] == 0.21


def test_letdraw_prior_league_specific():
    # 欧国联 |rq|=1 收缩值 0.291，高于全局 0.25
    assert letdraw_prior(-1, "欧国联") == LEAGUE_PRIOR_1["欧国联"]
    assert letdraw_prior(-1, "欧国联") > TIER_PRIOR[1]
    # 未知联赛回退到分档全局值
    assert letdraw_prior(-1, "不存在的联赛") == TIER_PRIOR[1]
    assert letdraw_prior(1, None) == TIER_PRIOR[1]
    # |rq|=2 用分档值（分联赛样本不足）
    assert letdraw_prior(-2, "欧国联") == TIER_PRIOR[2]


def test_calibrate_blends_toward_prior():
    # 模型低估场景：raw p_draw=0.185，先验 0.25，strength=0.5 → 0.2175
    ph, pd, pa = calibrate_handicap_1x2(0.5, 0.185, 0.315, -1, strength=0.5)
    assert abs(pd - 0.2175) < 1e-9
    assert abs(ph + pd + pa - 1.0) < 1e-9
    # H/A 按原比例重归一
    assert abs(ph / pa - 0.5 / 0.315) < 1e-9


def test_calibrate_strength_zero_is_identity():
    ph, pd, pa = calibrate_handicap_1x2(0.5, 0.185, 0.315, -1, strength=0.0)
    assert (ph, pd, pa) == (0.5, 0.185, 0.315)


def test_calibrate_strength_one_uses_prior_fully():
    ph, pd, pa = calibrate_handicap_1x2(0.5, 0.185, 0.315, -1,
                                        league="欧国联", strength=1.0)
    assert abs(pd - LEAGUE_PRIOR_1["欧国联"]) < 1e-9
    assert abs(ph + pd + pa - 1.0) < 1e-9


def test_calibrate_preserves_ordering():
    # 单调性：raw p_draw 越大，校准后也越大（同一先验/权重下）
    _, pd1, _ = calibrate_handicap_1x2(0.5, 0.15, 0.35, -1, strength=0.5)
    _, pd2, _ = calibrate_handicap_1x2(0.5, 0.20, 0.30, -1, strength=0.5)
    assert pd2 > pd1


def test_letdraw_guard_flags_high_draw_prob():
    g = letdraw_guard(0.35, 0.25, 0.40, -1)
    assert "防让平" in g["flags"]
    assert g["top"] == "让负"


def test_letdraw_guard_no_flag_when_low_draw():
    g = letdraw_guard(0.60, 0.15, 0.25, -1)
    assert "防让平" not in g["flags"]


def test_letdraw_guard_no_flag_when_rq_not_one():
    # |rq|=2 时不触发防让平标注（规则只针对 |rq|=1）
    g = letdraw_guard(0.50, 0.25, 0.25, -2)
    assert "防让平" not in g["flags"]


def test_letdraw_guard_downgrade_on_close_probs():
    g = letdraw_guard(0.36, 0.33, 0.31, -1)
    assert g["downgrade"] is True
    assert g["gap"] < 0.15
    g2 = letdraw_guard(0.60, 0.20, 0.20, -1)
    assert g2["downgrade"] is False
