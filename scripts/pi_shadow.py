#!/usr/bin/env python3
"""Pi-rating 影子评估：按开球时间重放 settlements，每场先记录
"当时评分会预测什么"，再更新评分。one-step-ahead，无前视偏差。

输出：样本数、Brier、log-loss、方向命中率（只统计有信号的场次）。
上线门槛：样本 >= 100 且持续优于/持平现有 Elo 信号才考虑接入融合。
"""
import math
import os
import sys
import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.pi_ratings import PiRatingStore  # noqa: E402

DB = os.environ.get("DATABASE_URL")


def brier(p, outcome: int) -> float:
    return sum((pi - (1 if i == outcome else 0)) ** 2
               for i, pi in enumerate(p))


def main() -> int:
    if not DB:
        print("DATABASE_URL 未设置，跳过")
        return 0
    store = PiRatingStore()
    n, brier_sum, ll_sum, hits = 0, 0.0, 0.0, 0
    with psycopg.connect(DB) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT m.home_team, m.away_team,
                          s.home_goals_full, s.away_goals_full
                   FROM settlements s
                   JOIN matches m ON m.match_id = s.match_id
                   WHERE s.home_goals_full IS NOT NULL
                   ORDER BY m.kickoff_at ASC""")
            rows = cur.fetchall()
    for home, away, hg, ag in rows:
        if not home or not away or hg is None:
            continue
        proba = store.predict_proba(home, away)  # 先预测（当时状态）
        store.update(home, away, hg, ag)          # 再学习
        if proba is None:
            continue
        outcome = 0 if hg > ag else (1 if hg == ag else 2)
        n += 1
        brier_sum += brier(proba, outcome)
        ll_sum += -math.log(max(proba[outcome], 1e-12))
        if max(range(3), key=lambda i: proba[i]) == outcome:
            hits += 1
    print("=== Pi-rating 影子评估（one-step-ahead）===")
    print(f"有信号样本: {n}")
    if n:
        print(f"Brier: {brier_sum / n:.4f}  LogLoss: {ll_sum / n:.4f}  "
              f"方向命中: {hits}/{n} ({hits / n:.1%})")
    if n < 100:
        print("样本 < 100：只看不调，不接入融合")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
