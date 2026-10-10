"""红黄牌 MVP 测试。"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.cards import _pois_over, predict_cards
from engine.predictor import _cards_block, predict


def _future_ts(**kw):
    return (datetime.now().astimezone() + timedelta(**kw)).isoformat()


def _payload(**kw):
    p = {
        "home": "阿森纳", "away": "曼城", "competition": "英超",
        "kickoff_at": _future_ts(days=2),
        "snapshot_at": _future_ts(seconds=-1),
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


def test_recent_factor_no_data_fallback():
    """无近期数据时因子=1.0，不影响结果。"""
    import engine.cards as cm
    cm._RECENT = []  # 模拟空数据
    try:
        r = predict_cards("Arsenal", "Chelsea", "E0")
        assert r["status"] == "ok"
        assert r["recent_form"]["home_factor"] == 1.0
        assert r["recent_form"]["away_factor"] == 1.0
        assert any("无近期牌数数据" in w for w in r["warnings"])
    finally:
        cm._RECENT = None


def test_recent_factor_direction():
    """近期黄牌多→因子>1；少→因子<1。"""
    import json, tempfile
    import engine.cards as cm
    recs = [{"team": "Arsenal", "date": f"2026-09-{10+i}", "league": "E0",
             "yellow": 6, "red": 0, "venue": "H"} for i in range(5)]
    recs += [{"team": "Chelsea", "date": f"2026-09-{10+i}", "league": "E0",
              "yellow": 0, "red": 0, "venue": "A"} for i in range(5)]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(recs, fh)
        tmp = fh.name
    old = cm._RECENT
    cm._RECENT = recs
    try:
        f_h, n_h = cm._recent_yellow_factor("Arsenal", "E0", 2.0)
        f_a, n_a = cm._recent_yellow_factor("Chelsea", "E0", 2.0)
        assert n_h == 5 and n_a == 5
        assert f_h > 1.0, f"高牌队因子应>1，实际{f_h}"
        assert f_a < 1.0, f"低牌队因子应<1，实际{f_a}"
        # 收缩：因子不会极端
        assert f_h < 1.5 and f_a > 0.7
    finally:
        cm._RECENT = old
        os.unlink(tmp)


def test_recent_factor_team_matching():
    """ESPN全名能匹配到简称。"""
    import engine.cards as cm
    assert cm._match_team("AFC Bournemouth", ["Bournemouth", "Arsenal"]) == "Bournemouth"
    assert cm._match_team("Leeds United", ["Leeds", "Arsenal"]) == "Leeds"
    assert cm._match_team("Manchester United", ["Man United", "Arsenal"]) == "Man United"
    assert cm._match_team("不存在的队", ["Arsenal"]) is None
