"""MVP-5 Pi-rating 封装测试（penaltyblog，影子模式）。"""
import os
import sys

import pytest

penaltyblog = pytest.importorskip("penaltyblog",
                                   reason="penaltyblog 未安装（.venv 外跳过）")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.pi_ratings import PiRatingStore


def _trained():
    s = PiRatingStore()
    s.update("浦和红钻", "富山胜利", 2, 0)
    s.update("富山胜利", "浦和红钻", 0, 3)
    s.update("浦和红钻", "大阪樱花", 1, 1)
    return s


def test_unknown_both_no_signal():
    s = PiRatingStore()
    assert s.predict_proba("甲", "乙") is None


def test_one_side_unknown_ok():
    s = PiRatingStore()
    s.update("浦和红钻", "富山胜利", 2, 0)
    p = s.predict_proba("浦和红钻", "从未见过的队")
    assert p is not None and abs(sum(p) - 1.0) < 1e-9


def test_proba_sums_to_one_and_plain_floats():
    s = _trained()
    p = s.predict_proba("富山胜利", "浦和红钻")
    assert p is not None
    assert abs(sum(p) - 1.0) < 1e-9
    assert all(type(x) is float for x in p)
    # 浦和明显更强，客胜概率应最高
    assert p[2] == max(p)


def test_update_is_zero_sum():
    # α≠β 时主客场各自不守恒，但球队总分（主+客）在对阵双方之间零和
    s = _trained()
    total = sum(r["home"] + r["away"] for r in s.to_state().values())
    assert abs(total) < 1e-9


def test_state_roundtrip():
    s = _trained()
    s2 = PiRatingStore()
    s2.from_state(s.to_state())
    assert s2.to_state() == s.to_state()
    assert s2.rating("浦和红钻")["matches"] == 3


def test_rating_none_for_unknown():
    assert PiRatingStore().rating("不存在") is None


def test_db_roundtrip():
    psycopg = pytest.importorskip("psycopg")
    db = os.environ.get("DATABASE_URL")
    if not db:
        pytest.skip("无 DATABASE_URL")
    s = _trained()
    with psycopg.connect(db) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS pi_ratings (team TEXT PRIMARY KEY, rating_home DOUBLE PRECISION NOT NULL DEFAULT 0, rating_away DOUBLE PRECISION NOT NULL DEFAULT 0, matches INT NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        s.save_to_db(conn)
        s2 = PiRatingStore()
        s2.load_from_db(conn)
    assert s2.to_state() == s.to_state()
