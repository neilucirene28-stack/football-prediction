#!/usr/bin/env python3
"""重跑朝鲜女足 vs 中国女足预测（2026-09-29 14:00，match 3095429）。

- 数据源：Titan007 公开免费模块（实时抓取）
- 不使用任何主观伤停系数（之前 away_attack=0.93 已移除）
- 王妍雯停赛仅作为文字备注保留
- 覆盖 football_v2.predictions 中对应 match_id 的记录
"""
import sys, os, json

sys.path.insert(0, "/opt/football-v2")
os.environ.setdefault("TITAN_REQUEST_DELAY", "0.4")

from collector.collector.sources.titan007 import Titan007Source
from engine.jingcai_predictor import predict
from api.api import config as api_config
import psycopg

MATCHID = "3095429"

src = Titan007Source(max_matches=5, days_ahead=2, request_delay=0.4)
info = src.parse_detail(src._get(f"https://live.titan007.com/detail/{MATCHID}cn.htm"), MATCHID)
m = src._build_match(info)
print("match:", m["home_team"], "vs", m["away_team"], m["kickoff_at"], flush=True)

payload = dict(m)
payload["home"] = m["home_team"]
payload["away"] = m["away_team"]
assert "injury" not in payload, "payload 不应含伤停系数"

res = predict(payload, api_config.ENGINE_CONFIG)
print("probs:", res["p_home"], res["p_draw"], res["p_away"], flush=True)
print("lambdas:", res["lambda_home"], res["lambda_away"],
      "completeness:", res["completeness"], res["grade"], flush=True)

notes = {
    "rerun_at": "2026-09-29",
    "source": "titan007",
    "external_id": m["external_id"],
    "injury_note": "王妍雯累计两黄停赛（文字备注，未设任何伤停系数）；张琳艳伤缺成疑（未确认）",
    "no_subjective_factor": True,
}
full_payload = dict(res)
full_payload["notes"] = notes

db_url = open("/opt/football-v2/.database_url").read().strip()
with psycopg.connect(db_url) as conn, conn.cursor() as cur:
    cur.execute(
        "SELECT id FROM matches WHERE external_id=%s AND source=%s",
        (m["external_id"], "titan007"))
    row = cur.fetchone()
    if row:
        mid = row[0]
    else:
        raw = dict(m["raw"])
        raw["neutral_site"] = m.get("neutral_site")
        cur.execute(
            """INSERT INTO matches
               (external_id, source, competition, home_team, away_team,
                kickoff_at, raw)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (m["external_id"], "titan007", m["competition"],
             m["home_team"], m["away_team"], m["kickoff_at"],
             json.dumps(raw, ensure_ascii=False)))
        mid = cur.fetchone()[0]
    print("db match_id:", mid, flush=True)
    cur.execute("DELETE FROM predictions WHERE match_id=%s", (mid,))
    cur.execute(
        """INSERT INTO predictions
           (match_id, p_home, p_draw, p_away, lambda_home, lambda_away,
            completeness, grade, confidence, payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (mid, res["p_home"], res["p_draw"], res["p_away"],
         res["lambda_home"], res["lambda_away"],
         res["completeness"], res["grade"], res["confidence"],
         json.dumps(full_payload, ensure_ascii=False)))
    # 同时清理旧的 xiaodianhuo 来源的那条记录（match_id=1），避免混淆
    cur.execute(
        """UPDATE predictions SET payload = payload || %s
           WHERE match_id=1 AND NOT (payload ? 'no_subjective_factor')""",
        (json.dumps({"superseded_by": "titan007 rerun 2026-09-29, away_attack=0.93 removed"}),))
    print("predictions updated, rows for match:", mid, flush=True)
print("DONE")
