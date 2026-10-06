"""阵容因子使用纪律（v2.5）测试。

注意：lineup 模块暂不接入生产 predict()（历史数据无阵容标注，无法验证），
此处仅测试模块本身的纪律逻辑。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.lineup import classify_absence, lineup_adjustment


def test_qualifying_absence_cuts_lambda():
    ab = {"team": "home", "player": "莱万", "is_top2_scorer": True,
          "has_replacement": False}
    assert classify_absence(ab) == "cut_lambda"
    out = lineup_adjustment([ab])
    assert out["home_lambda_mult"] == 0.875  # 下调 12.5%
    assert out["away_lambda_mult"] == 1.0
    assert "莱万" in out["qualifying"]


def test_top_scorer_with_replacement_no_cut():
    # 射手榜前二但有对位替代 → 只调离散度（002 法国无姆巴佩案例）
    ab = {"team": "home", "player": "姆巴佩", "is_top2_scorer": True,
          "has_replacement": True}
    assert classify_absence(ab) == "dispersion_only"
    out = lineup_adjustment([ab])
    assert out["home_lambda_mult"] == 1.0
    assert out["dispersion_bump"] > 0


def test_ordinary_absence_dispersion_only():
    ab = {"team": "away", "player": "某轮换后卫", "is_top2_scorer": False,
          "has_replacement": True}
    assert classify_absence(ab) == "dispersion_only"
    out = lineup_adjustment([ab])
    assert out["home_lambda_mult"] == 1.0
    assert out["away_lambda_mult"] == 1.0
    assert out["dispersion_bump"] > 0


def test_incomplete_info_ignored():
    assert classify_absence({}) == "ignore"
    assert classify_absence(None) == "ignore"
    assert classify_absence({"team": "home"}) == "ignore"
    out = lineup_adjustment([{}])
    assert out["home_lambda_mult"] == 1.0
    assert out["dispersion_bump"] == 0.0


def test_empty_absences_noop():
    out = lineup_adjustment([])
    assert out == {"home_lambda_mult": 1.0, "away_lambda_mult": 1.0,
                   "dispersion_bump": 0.0, "qualifying": [], "notes": []}
    out2 = lineup_adjustment(None)
    assert out2["home_lambda_mult"] == 1.0


def test_multiple_absences_stack():
    absences = [
        {"team": "home", "player": "A", "is_top2_scorer": True,
         "has_replacement": False},
        {"team": "home", "player": "B", "is_top2_scorer": True,
         "has_replacement": False},
    ]
    out = lineup_adjustment(absences)
    # 两次 0.875 连乘（模块内保留 4 位小数）
    assert abs(out["home_lambda_mult"] - 0.875 ** 2) < 1e-3
    assert len(out["qualifying"]) == 2
