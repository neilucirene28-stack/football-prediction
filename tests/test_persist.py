import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.api.persist import (  # noqa: E402
    build_prediction_row, save_prediction, save_settlement, PRED_NAMESPACE)
from engine.predictor import model_version, predict  # noqa: E402


def _mk(n_home, n_away, venue):
    return [{"gf": n_home, "ga": n_away, "venue": venue} for _ in range(8)]


def _future_ts(**kw):
    return (datetime.now().astimezone() + timedelta(**kw)).isoformat()


def sample_payload(**kw):
    p = {
        "home": "土耳其", "away": "意大利", "competition": "欧国联",
        "kickoff_at": _future_ts(days=2),
        "snapshot_at": _future_ts(days=1),
        "league_avg_goals": 2.70,
        "home_recent": _mk(2, 1, "H"),
        "away_recent": _mk(1, 1, "A"),
        "odds": {"home": 2.69, "draw": 3.30, "away": 2.20},
        "opening_odds": {"home": 2.80, "draw": 3.20, "away": 2.30},
        "ou_line": 2.5,
    }
    p.update(kw)
    return p


def test_model_version_deterministic():
    cfg = {"rho": -0.13, "decay": 0.9}
    assert model_version(cfg) == model_version(cfg)
    assert model_version(cfg).startswith("v2.5+")


def test_model_version_changes_with_params():
    v1 = model_version({"rho": -0.13, "decay": 0.9})
    v2 = model_version({"rho": -0.13, "decay": 0.95})
    assert v1 != v2


def test_predict_result_carries_model_version():
    r = predict(sample_payload())
    assert r["status"] == "ok"
    assert r["model_version"].startswith("v2.5+")
    # 同一 payload 同一版本
    assert predict(sample_payload())["model_version"] == r["model_version"]


def test_build_prediction_row_fields():
    p, r = sample_payload(), predict(sample_payload())
    row = build_prediction_row(42, p, r, deterministic_day=date(2026, 9, 30))
    assert row["match_id"] == 42
    assert row["model_version"] == r["model_version"]
    assert row["league"] == "欧国联"
    assert row["p_home"] == r["p_home"]
    assert abs(r["p_home"] + r["p_draw"] + r["p_away"] - 1.0) < 5e-4
    import json
    assert json.loads(row["signals"])["model"] == r["signals"]["model"]
    assert json.loads(row["weights"]) == r["weights"]
    assert json.loads(row["odds_snapshot"])["opening_odds"]["home"] == 2.80
    assert json.loads(row["payload"])["input"]["home"] == "土耳其"  # input 快照
    assert json.loads(row["payload"])["result"]["p_home"] == r["p_home"]
    # 确定性幂等键
    expect = str(uuid.uuid5(PRED_NAMESPACE,
                            f"42:{r['model_version']}:2026-09-30"))
    assert row["prediction_id"] == expect


def test_build_prediction_row_random_without_day():
    p, r = sample_payload(), predict(sample_payload())
    a = build_prediction_row(1, p, r)["prediction_id"]
    b = build_prediction_row(1, p, r)["prediction_id"]
    assert a != b
    uuid.UUID(a)  # 合法 UUID


def _mock_conn(fetchone_result=None):
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchone.return_value = fetchone_result
    return conn, cur


def test_save_prediction_idempotent_sql():
    p, r = sample_payload(), predict(sample_payload())
    conn, cur = _mock_conn()
    pid = save_prediction(conn, 7, p, r, deterministic_day=date(2026, 9, 30))
    sql = cur.execute.call_args[0][0]
    assert "ON CONFLICT (prediction_id) DO NOTHING" in sql
    assert pid == build_prediction_row(
        7, p, r, deterministic_day=date(2026, 9, 30))["prediction_id"]
    conn.commit.assert_called()


def test_save_settlement_idempotent():
    conn, cur = _mock_conn(fetchone_result=("pid",))
    assert save_settlement(conn, "pid", 2, 1, ht_home=1, ht_away=0) is True
    sql = cur.execute.call_args[0][0]
    assert "ON CONFLICT (prediction_id) DO NOTHING" in sql

    conn2, cur2 = _mock_conn(fetchone_result=None)  # 已存在 → no-op
    assert save_settlement(conn2, "pid", 2, 1) is False


# ---------------- settle.py ----------------
from scripts.settle import parse_final_score  # noqa: E402


def test_parse_final_score_finished():
    html = ("xxx 上半场结束！比分：0-0^^^^yyy "
            "比赛结束！ 比分：2-0^^^^zzz")
    assert parse_final_score(html) == (2, 0, 0, 0)


def test_parse_final_score_no_ht():
    html = "比赛结束！ 比分：1-1^^^^"
    assert parse_final_score(html) == (1, 1, None, None)


def test_parse_final_score_not_finished():
    assert parse_final_score("直播中 1-0") is None
    assert parse_final_score("") is None


# ---------------- scoreboard.py ----------------
from scripts.scoreboard import score_one, aggregate  # noqa: E402


def _pred_row(**kw):
    p = {"p_home": 0.6, "p_draw": 0.25, "p_away": 0.15,
         "signals": {"model": [0.6, 0.25, 0.15],
                     "market": [0.5, 0.3, 0.2],
                     "elo": [0.55, 0.25, 0.2]},
         "derivatives": {
             "handicap_1x2": {"line": -1, "p_home": 0.5,
                              "p_draw": 0.2, "p_away": 0.3},
             "top_scores": [{"score": "2-0"}, {"score": "1-0"}, {"score": "2-1"}],
             "expected_goals": 2.8}}
    p.update(kw)
    return p


def test_score_one_direction_and_signals():
    s = score_one(_pred_row(), {"home_goals": 2, "away_goals": 0})
    assert s["direction_hit"] == 1
    assert s["top3_hit"] == 1
    assert s["handicap_hit"] == 1  # 净胜 2，让 -1 → 让胜
    assert s["goals_err"] == 0.8
    assert s["brier_model"] < s["brier_market"]  # model 更准 → Brier 更小
    assert 0 < s["logloss"] < 2


def test_score_one_miss():
    s = score_one(_pred_row(), {"home_goals": 0, "away_goals": 1})
    assert s["direction_hit"] == 0
    assert s["top3_hit"] == 0


def test_aggregate_averages():
    a = aggregate([score_one(_pred_row(), {"home_goals": 2, "away_goals": 0}),
                   score_one(_pred_row(), {"home_goals": 0, "away_goals": 1})])
    assert a["n"] == 2
    assert a["direction_hit"] == 0.5
    assert a["brier_model_n"] == 2
    assert aggregate([]) == {"n": 0}
