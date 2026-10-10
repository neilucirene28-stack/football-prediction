"""自学习回路的数据纪律（MVP-1 持久化 / MVP-2 结算共用）。

- predictions 不可变：写入后永不 UPDATE 概率、版本、信号；
  赛果只追加到 settlements。
- prediction_id 是幂等键：batch 用确定性 uuid5（同一天重复跑是 no-op）；
  API 交互式调用用随机 uuid4（每次调用都是一次独立预测事件）。
- 无前视：settle 只接受 kickoff 已过的比赛（由 settle.py 在 SQL 层保证）。
"""
import json
import uuid
from datetime import date, datetime, timezone
import math

PRED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "football-prediction-v2")

_PREDICTION_COLS = (
    "prediction_id", "match_id", "model_version", "league",
    "kickoff_at", "predicted_at",
    "p_home", "p_draw", "p_away",
    "lambda_home", "lambda_away",
    "completeness", "grade", "confidence",
    "signals", "weights", "derivatives", "odds_snapshot", "divergence",
    "payload",
)


def build_prediction_row(match_id: int, payload: dict, result: dict,
                         deterministic_day: date | None = None) -> dict:
    """拼装 predictions 行。result 必须是 predict() 的 ok 结果。"""
    if result.get("status") != "ok":
        raise ValueError("only successful predictions can be persisted")
    timing = result.get("prediction_timing") or {}
    if timing.get("mode") == "historical_replay":
        raise ValueError("historical replay cannot be stored as a prospective prediction")
    if timing.get("mode") != "prospective" or not timing.get("generated_at"):
        raise ValueError("prospective generation timestamp is required")
    written_at = datetime.now(timezone.utc)
    generated_at = datetime.fromisoformat(timing["generated_at"])
    kickoff = datetime.fromisoformat(payload["kickoff_at"])
    snapshot = datetime.fromisoformat(payload["snapshot_at"])
    if any(t.tzinfo is None for t in (generated_at, kickoff, snapshot)):
        raise ValueError("prediction times must carry timezones")
    if not snapshot <= generated_at <= written_at < kickoff:
        raise ValueError("prediction must be generated and stored before kickoff, after its snapshot")
    probs = result.get("p_final_full")
    if probs is None:
        probs = [result["p_home"], result["p_draw"], result["p_away"]]
    if len(probs) != 3 or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probs) or abs(sum(probs)-1) > 5e-4:
        raise ValueError("invalid WDL probabilities")
    match = result.get("match") or {}
    if any(match.get(k) != payload.get(k) for k in ("home", "away", "kickoff_at")):
        raise ValueError("prediction result does not match its input fixture")
    version = result.get("model_version", "unknown")
    if deterministic_day is not None:
        prediction_id = uuid.uuid5(
            PRED_NAMESPACE, f"{match_id}:{version}:{deterministic_day.isoformat()}")
    else:
        prediction_id = uuid.uuid4()
    odds = payload.get("odds") or {}
    return {
        "prediction_id": str(prediction_id),
        "match_id": match_id,
        "model_version": version,
        "league": payload.get("competition", "") or "",
        "kickoff_at": payload.get("kickoff_at"),
        "predicted_at": generated_at.isoformat(),
        "p_home": probs[0],
        "p_draw": probs[1],
        "p_away": probs[2],
        "lambda_home": result.get("lambda_home"),
        "lambda_away": result.get("lambda_away"),
        "completeness": result.get("completeness", 0),
        "grade": result.get("grade", "D"),
        "confidence": result.get("confidence", "X"),
        "signals": json.dumps(result.get("signals") or {}, ensure_ascii=False),
        "weights": json.dumps(result.get("weights") or {}, ensure_ascii=False),
        "derivatives": json.dumps(result.get("derivatives") or {}, ensure_ascii=False),
        "odds_snapshot": json.dumps({
            "odds": odds,
            "opening_odds": payload.get("opening_odds"),
            "asian": payload.get("asian"),
            "ou_line": payload.get("ou_line"),
            "market": result.get("market"),
        }, ensure_ascii=False, default=str),
        "divergence": (json.dumps(result.get("divergence"), ensure_ascii=False)
                       if result.get("divergence") else None),
        # 完整快照：input 供未来重放，result 供复盘（解决 2026-09-29 无赛前快照问题）
        "payload": json.dumps({"input": payload, "result": result},
                              ensure_ascii=False, default=str),
    }


def save_prediction(conn, match_id: int, payload: dict, result: dict,
                    deterministic_day: date | None = None) -> str:
    """幂等写入预测。返回 prediction_id。"""
    row = build_prediction_row(match_id, payload, result, deterministic_day)
    cols = ", ".join(_PREDICTION_COLS)
    placeholders = ", ".join(["%s"] * len(_PREDICTION_COLS))
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO predictions ({cols}) SELECT {placeholders} "
            "WHERE clock_timestamp() < %s::timestamptz "
            "ON CONFLICT (prediction_id) DO NOTHING",
            [row[c] for c in _PREDICTION_COLS] + [row["kickoff_at"]])
        if cur.rowcount == 0:
            cur.execute("SELECT prediction_id FROM predictions WHERE prediction_id=%s",
                        [row["prediction_id"]])
            if cur.fetchone() is None:
                raise ValueError("prediction reached database after kickoff")
    conn.commit()
    return row["prediction_id"]


def save_settlement(conn, prediction_id: str, home_goals: int, away_goals: int,
                    ht_home: int | None = None, ht_away: int | None = None,
                    source: str = "") -> bool:
    """幂等写入赛果。已存在返回 False（no-op），新写入返回 True。"""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO settlements
               (prediction_id, home_goals, away_goals, ht_home, ht_away, source)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (prediction_id) DO NOTHING
               RETURNING prediction_id""",
            (prediction_id, home_goals, away_goals, ht_home, ht_away, source))
        inserted = cur.fetchone() is not None
    conn.commit()
    return inserted
