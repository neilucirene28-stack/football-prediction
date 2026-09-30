"""红黄牌 MVP 测试。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.cards import _pois_over, predict_cards
from engine.predictor import _cards_block, predict


def _payload(**kw):
    p = {
        "home": "阿森纳", "away": "曼城", "competition": "英超",
        "kickoff_at": "2026-10-05T02:45:00+08:00",
        "snapshot_at": "2026-10-04T20:00:00+08:00",
        "league_avg_goals": 2.70,
        "home_recent": [{"gf": 2, "ga": 1, "venue": "H"} for _ in range(8)],
        "away_recent": [{"gf": 1, "ga": 1, "venue": "A"} for _ in range(8)],
        "odds": {"home": 2.69, "draw": 3.30, "away": 2.20},
        "ou_line": 2.5,
    }
    p.update(kw)
    return p


def test_predict_cards_ok():
    r = predict_cards("Arsenal", "Man City", "E0")
    assert r["status"] == "ok"
    assert r["league"] == "英超"
    assert abs(r["exp_total_yellow"] -
               (r["exp_home_yellow"] + r["exp_away_yellow"])) < 0.02
    assert 0 <= r["p_over_3_5"] <= 1 and 0 <= r["p_over_4_5"] <= 1
    assert r["p_over_3_5"] > r["p_over_4_5"]
    assert 0 <= r["p_red"] <= 1
    # booking points 近似 = 10*黄 + 25*红
    assert abs(r["exp_booking_points"] -
               (10 * r["exp_total_yellow"] +
                25 * (1 - (1 - r["p_red"])))) < 30  # 量级检查


def test_predict_cards_unknown_team():
    r = predict_cards("不存在的队", "Man City", "E0")
    assert r["status"] == "insufficient_data"
    assert "不存在的队" in r["reason"]


def test_predict_cards_unknown_league():
    r = predict_cards("Arsenal", "Man City", "J2")
    assert r["status"] == "insufficient_data"


def test_predict_cards_no_referee_defaults():
    r = predict_cards("Arsenal", "Man City", "E0")
    assert r["referee"]["used"] is False
    assert r["referee"]["multiplier"] == 1.0
    assert r["confidence"] == "low"
    assert any("裁判" in w for w in r["warnings"])


def test_predict_cards_referee_effect_bounded():
    # M Oliver 偏松哨(3.45<均值3.8)，C Pawson 偏严(4.11>均值)：乘子应反向
    r_loose = predict_cards("Arsenal", "Man City", "E0", referee="M Oliver")
    r_strict = predict_cards("Arsenal", "Man City", "E0", referee="C Pawson")
    assert r_loose["referee"]["used"] is True
    assert r_loose["referee"]["multiplier"] < 1.0 < \
        r_strict["referee"]["multiplier"]
    # 权重 0.25 封顶：乘子偏离不超过约 ±8%
    assert 0.9 < r_strict["referee"]["multiplier"] < 1.1


def test_predict_cards_referee_too_few_matches():
    r = predict_cards("Arsenal", "Man City", "E0", referee="查无此人")
    assert r["referee"]["used"] is False


def test_pois_over_sanity():
    # λ=3.8 时 P(>3.5) 应约 0.5 上方
    assert 0.45 < _pois_over(3.8, 3.5) < 0.60
    assert _pois_over(0.0, 3.5) == 0.0


def test_cards_block_alias_cn_to_en():
    b = _cards_block({"home": "阿森纳", "away": "曼城",
                      "cards": {"league_code": "E0"}})
    assert b["status"] == "ok"
    assert b["league"] == "英超"


def test_cards_block_unknown_cn_name():
    b = _cards_block({"home": "富山胜利", "away": "曼城",
                      "cards": {"league_code": "E0"}})
    assert b["status"] == "insufficient_data"
    assert "富山胜利" in b["reason"]


def test_cards_block_no_league_code():
    b = _cards_block({"home": "阿森纳", "away": "曼城"})
    assert b["status"] == "insufficient_data"


def test_predict_includes_cards_derivative():
    r = predict(_payload(cards={"league_code": "E0"}))
    assert r["status"] == "ok"
    c = r["derivatives"]["cards"]
    assert c["status"] == "ok"
    assert c["exp_total_yellow"] > 0


def test_predict_cards_insufficient_for_jleague():
    r = predict(_payload(home="富山胜利", away="大阪樱花",
                         cards={"league_code": "E0"}))
    assert r["derivatives"]["cards"]["status"] == "insufficient_data"
