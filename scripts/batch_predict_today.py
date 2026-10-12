#!/usr/bin/env python3
"""批量预测今日比赛：读 /tmp/today_matches.json，用 Titan007 全量数据跑引擎。

输出每场的：比分 / 让球胜平负 / 半场胜平负 / 全场胜平负 / 进球数
不使用任何主观伤停系数。已开赛的比赛引擎会自动拒绝。
"""
import sys, json, time, os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.collector.sources.titan007 import Titan007Source
from engine.jingcai_predictor import predict
from engine.jingcai_batch import with_official_handicap, handicap_output
from engine.jingcai_calibration import load_form_only_config
from api.api.persist import save_prediction

try:
    import psycopg
except ImportError:
    psycopg = None

# form-only 校准参数（无市场信号时启用；有市场时不用，避免过度收缩）
_CALIBRATION_CONFIG, _CALIBRATION_SELECTION = load_form_only_config()

def _db_conn():
    """best-effort：无 DB 时返回 None，预测照常输出到 /tmp。"""
    url = os.environ.get("DATABASE_URL")
    if not url or not psycopg:
        return None
    try:
        return psycopg.connect(url)
    except Exception as e:
        print(f"DB connect failed (continue without persistence): {type(e).__name__}", flush=True)
        return None


def _upsert_match(cur, m):
    cur.execute(
        """INSERT INTO matches
           (external_id, source, competition, home_team, away_team, kickoff_at, raw)
           VALUES (%s,'titan007',%s,%s,%s,%s,%s)
           ON CONFLICT (external_id, source) DO UPDATE SET
               competition=EXCLUDED.competition, kickoff_at=EXCLUDED.kickoff_at,
               raw=EXCLUDED.raw
           RETURNING id""",
        (m.get("external_id"), m.get("competition", ""), m["home_team"],
         m["away_team"], m["kickoff_at"],
         json.dumps(m.get("raw", {}), ensure_ascii=False, default=str)))
    return cur.fetchone()[0]


def main(input_path="/tmp/today_matches.json", output_path="/tmp/today_predictions.json"):
    with open(input_path) as f:
        matches = json.load(f)
    print(f"matches to predict: {len(matches)}", flush=True)
    conn = _db_conn()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if conn:
        print("DB persistence enabled (self-learning loop)", flush=True)
    src = Titan007Source(request_delay=0.5)
    results = []
    for i, mm in enumerate(matches):
        mid = mm["matchid"]
        try:
            info = src.parse_detail(src._get(f"https://live.titan007.com/detail/{mid}cn.htm"), mid)
            m = src._build_match(info)
            if not m:
                print(f"[{i}] {mid} build failed", flush=True); continue
            p = dict(m); p["home"] = m["home_team"]; p["away"] = m["away_team"]
            p = with_official_handicap(p, mm)
            if "cards" in mm:
                if p.get("cards") and p["cards"] != mm["cards"]:
                    raise ValueError("比赛列表与采集快照的牌数输入冲突")
                p["cards"] = mm["cards"]
            # 仅带版本和时序证据的校准候选才能提交给预测器
            cfg = _CALIBRATION_CONFIG if not p.get("odds") else None
            res = predict(p, cfg)
            if res.get("status") == "insufficient_data":
                print(f"[{i}] {m['home_team']} vs {m['away_team']} 数据不足", flush=True); continue
            # 自学习回路：持久化完整预测记录（含 input 快照 + model_version），幂等
            persistence = {"status": "not_saved", "reason": "database_unavailable"}
            if conn:
                try:
                    with conn.cursor() as cur:
                        db_match_id = _upsert_match(cur, m)
                    prediction_id = save_prediction(conn, db_match_id, payload=p, result=res,
                                                    deterministic_day=today)
                    persistence = {"status": "stored_or_existing", "prediction_id": prediction_id}
                except Exception as e:
                    conn.rollback()
                    persistence = {"status": "not_saved", "reason": "write_failed",
                                   "error_type": type(e).__name__}
                    print(f"[{i}] persist ERROR {type(e).__name__}", flush=True)
            res["persistence"] = persistence
            d = res["derivatives"]
            h1x2 = handicap_output(res)
            out = {
                "kickoff": m["kickoff_at"], "competition": m["competition"],
                "home": m["home_team"], "away": m["away_team"],
                "neutral": bool(m.get("neutral_site")),
                "p_home": res["p_home"], "p_draw": res["p_draw"], "p_away": res["p_away"],
                "lambda_home": res["lambda_home"], "lambda_away": res["lambda_away"],
                "completeness": res["completeness"], "grade": res["grade"],
                "confidence": res["confidence"],
                "top_scores": d["top_scores"][:5],
                "upset_score": d.get("upset_score"),
                "half_time": d["half_time"],
                "handicap_1x2": h1x2,
                "asian": d["asian"],
                "over_under": d["over_under"],
                "expected_goals": d["expected_goals"],
                "main_goal_interval": d["main_goal_interval"],
                "total_goals": d["total_goals"],
                "btts": d["btts"],
                "cards": d["cards"],
                "issues": res.get("consistency_issues"),
                "model_version": res["model_version"],
                "calibration_selection": _CALIBRATION_SELECTION,
                "input": p, "prediction": res,
            }
            results.append(out)
            print(f"[{i}] {m['kickoff_at'][11:16]} {m['home_team']} vs {m['away_team']} "
                  f"{res['p_home']:.2f}/{res['p_draw']:.2f}/{res['p_away']:.2f} "
                  f"grade={res['grade']}", flush=True)
        except Exception as e:
            print(f"[{i}] {mid} ERROR {type(e).__name__}: {e}", flush=True)
        time.sleep(0.3)
    with open(output_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    if conn:
        conn.close()
    print(f"DONE: {len(results)} predictions -> {output_path}", flush=True)

if __name__ == "__main__":
    main()
