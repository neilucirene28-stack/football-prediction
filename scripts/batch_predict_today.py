#!/usr/bin/env python3
"""批量预测今日比赛：读 /tmp/today_matches.json，用 Titan007 全量数据跑引擎。

输出每场的：比分 / 让球胜平负 / 半场胜平负 / 全场胜平负 / 进球数
不使用任何主观伤停系数。已开赛的比赛引擎会自动拒绝。
"""
import sys, json, time, os
from datetime import date
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from collector.collector.sources.titan007 import Titan007Source
from engine.predictor import predict
from engine.poisson import handicap_1x2, score_matrix
from api.api.persist import save_prediction

try:
    import psycopg
except ImportError:
    psycopg = None

# form-only 校准参数（无市场信号时启用；有市场时不用，避免过度收缩）
try:
    _cal = json.load(open("/home/hatch/workspace/football-prediction-v2/engine/calibration.json"))
    _PLATT_FORM_ONLY = {k: tuple(v) for k, v in _cal["form_only"].items()}
except Exception:
    _PLATT_FORM_ONLY = None

def _db_conn():
    """best-effort：无 DB 时返回 None，预测照常输出到 /tmp。"""
    url = os.environ.get("DATABASE_URL")
    if not url or not psycopg:
        return None
    try:
        return psycopg.connect(url)
    except Exception as e:
        print(f"DB connect failed (continue without persistence): {e}", flush=True)
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


def main():
    with open("/tmp/today_matches.json") as f:
        matches = json.load(f)
    print(f"matches to predict: {len(matches)}", flush=True)
    conn = _db_conn()
    today = date.today()
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
            # 无市场信号时启用 form-only Platt 校准
            cfg = {"platt": _PLATT_FORM_ONLY} if (
                _PLATT_FORM_ONLY and not p.get("odds")) else None
            res = predict(p, cfg)
            if res.get("status") == "insufficient_data":
                print(f"[{i}] {m['home_team']} vs {m['away_team']} 数据不足", flush=True); continue
            # 自学习回路：持久化完整预测记录（含 input 快照 + model_version），幂等
            if conn:
                try:
                    with conn.cursor() as cur:
                        mid = _upsert_match(cur, m)
                    save_prediction(conn, mid, payload=p, result=res,
                                    deterministic_day=today)
                except Exception as e:
                    print(f"[{i}] persist ERROR {type(e).__name__}: {e}", flush=True)
            d = res["derivatives"]
            # 让球胜平负：按整数让球线算（取市场亚盘就近整数）
            h1x2 = None
            ah = (res.get("derivatives", {}).get("asian") or {}).get("handicap")
            if ah is not None:
                line = int(round(ah))
                mx = score_matrix(res["lambda_home"], res["lambda_away"])
                h, dr, a = handicap_1x2(mx, line)
                h1x2 = {"line": line, "p_home": round(h, 4),
                        "p_draw": round(dr, 4), "p_away": round(a, 4)}
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
                "issues": res.get("issues"),
            }
            results.append(out)
            print(f"[{i}] {m['kickoff_at'][11:16]} {m['home_team']} vs {m['away_team']} "
                  f"{res['p_home']:.2f}/{res['p_draw']:.2f}/{res['p_away']:.2f} "
                  f"grade={res['grade']}", flush=True)
        except Exception as e:
            print(f"[{i}] {mid} ERROR {type(e).__name__}: {e}", flush=True)
        time.sleep(0.3)
    with open("/tmp/today_predictions.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    if conn:
        conn.close()
    print(f"DONE: {len(results)} predictions -> /tmp/today_predictions.json", flush=True)

main()
