"""v2.2 优化点的测试：H2H 生效、漂移影响信心、Platt 接入预测。"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.predictor import predict

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _payload(**kw):
    now = datetime.now(TZ)
    hr = [{"gf": 2, "ga": 1, "venue": "H"}] * 8
    ar = [{"gf": 1, "ga": 1, "venue": "A"}] * 8
    p = {
        "home": "主队", "away": "客队", "competition": "测试联赛",
        "kickoff_at": (now + timedelta(days=2)).isoformat(),
        "snapshot_at": now.isoformat(),
        "league_avg_goals": 2.70,
        "home_recent": hr, "away_recent": ar,
        "odds": {"home": 2.10, "draw": 3.40, "away": 3.60},
        "ou_line": 2.5,
    }
    p.update(kw)
    return p


def test_h2h_adjusts_lambdas():
    base = predict(_payload())
    h2h = [{"gf": 3, "ga": 0}, {"gf": 2, "ga": 1}, {"gf": 2, "ga": 0}]
    with_h2h = predict(_payload(h2h=h2h))
    assert with_h2h["lambda_notes"]["h2h_adjust"] == 0.03  # 净胜球+6 → 封顶+3%
    assert with_h2h["lambda_home"] > base["lambda_home"]
    assert with_h2h["lambda_away"] < base["lambda_away"]


def test_h2h_capped_and_negative():
    bad = [{"gf": 0, "ga": 4}, {"gf": 0, "ga": 3}]
    r = predict(_payload(h2h=bad))
    assert r["lambda_notes"]["h2h_adjust"] == -0.03  # 封顶-3%
    assert r["lambda_home"] < predict(_payload())["lambda_home"]


def test_drift_lowers_confidence():
    stable = predict(_payload())
    drifting = predict(_payload(opening_odds={"home": 2.60, "draw": 3.40,
                                              "away": 2.80}))
    assert drifting["market"]["drift"]["signals"]  # 初盘→即时大幅漂移
    assert drifting["confidence_score"] < stable["confidence_score"]


def test_platt_applied_in_predict():
    # 构造"拉低主胜"的校准参数（B为负 → 往下拉）
    platt = {"home": (1.0, -0.8), "draw": (1.0, 0.0), "away": (1.0, 0.0)}
    base = predict(_payload())
    cal = predict(_payload(), config={"platt": platt})
    assert cal["calibrated"] is True
    assert base["calibrated"] is False
    assert abs(cal["p_home"] + cal["p_draw"] + cal["p_away"] - 1.0) < 1e-6
    assert cal["p_home"] < base["p_home"]  # 过度自信被往下拉
    assert any("Platt" in n for n in cal["notes"])
