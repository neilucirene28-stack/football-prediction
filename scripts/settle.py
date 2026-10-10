#!/usr/bin/env python3
"""每日结算：把已完赛预测的比分写入 settlements（幂等、只追加）。

数据纪律：
- 只结算 kickoff 已过 2 小时且详情页出现"比赛结束"标记的比赛（无前视）。
- 解析失败/未完赛就跳过，等下一轮；绝不写估算比分。
- settlements 已有记录时 no-op；永不 UPDATE predictions。
"""
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api.api.jingcai_settlement import save_jingcai_settlement  # noqa: E402

try:
    import psycopg
except ImportError:
    psycopg = None

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_DETAIL_URL = "https://live.titan007.com/detail/{mid}cn.htm"
_DELAY = 0.5


def parse_final_score(html: str):
    """解析完赛比分。返回 (hg, ag, ht_hg, ht_ag)；未完赛返回 None。"""
    m = re.search(r"比赛结束！\s*比分：(\d+)-(\d+)", html)
    if not m:
        return None
    hg, ag = int(m.group(1)), int(m.group(2))
    ht = re.search(r"上半场结束！比分：(\d+)-(\d+)", html)
    ht_hg, ht_ag = (int(ht.group(1)), int(ht.group(2))) if ht else (None, None)
    return hg, ag, ht_hg, ht_ag


def fetch_detail(mid: str) -> str:
    req = urllib.request.Request(
        _DETAIL_URL.format(mid=mid),
        headers={"User-Agent": _UA, "Referer": "https://live.titan007.com/"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def pending_predictions(cur):
    cur.execute(
        """SELECT p.prediction_id, m.external_id
           FROM predictions p
           JOIN matches m ON m.id = p.match_id
           LEFT JOIN settlements s ON s.prediction_id = p.prediction_id
           WHERE s.prediction_id IS NULL
             AND p.payload->'result'->>'model'='jingcai'
             AND COALESCE(p.payload->'result'->>'project_scope','jingcai')='jingcai'
             AND p.kickoff_at < now() - interval '2 hours'
             AND m.external_id LIKE 'titan-%%'
           ORDER BY p.kickoff_at""")
    return cur.fetchall()


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url or not psycopg:
        print("no DATABASE_URL or psycopg; nothing to settle")
        return 0
    conn = psycopg.connect(url)
    with conn.cursor() as cur:
        rows = pending_predictions(cur)
    print(f"pending settlements: {len(rows)}", flush=True)
    settled, skipped, failed = 0, 0, 0
    for prediction_id, external_id in rows:
        mid = external_id.split("titan-", 1)[1]
        try:
            html = fetch_detail(mid)
            score = parse_final_score(html)
            if score is None:
                skipped += 1
                continue
            hg, ag, ht_hg, ht_ag = score
            if save_jingcai_settlement(conn, str(prediction_id), hg, ag,
                               ht_home=ht_hg, ht_away=ht_ag, source="titan007"):
                settled += 1
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            print(f"{external_id} ERROR {type(e).__name__}: {e}", flush=True)
        time.sleep(_DELAY)
    conn.close()
    print(f"DONE: settled={settled} skipped={skipped} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
