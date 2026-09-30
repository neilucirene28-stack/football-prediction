#!/usr/bin/env python3
"""赛后更新 Pi-rating：从 settlements 读已结算比赛，按开球时间顺序更新评分。

幂等：pi_ratings 是评分快照表，重复跑会重复累积 → 用 settlements.settled_at
只处理 settled_at > 上次运行水位线的记录，水位线存在 pi_ratings_meta。
简单起见：每次全量重放（truncate 后按 kickoff 顺序重算），比赛量小可接受。
"""
import os
import sys
import psycopg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.pi_ratings import PiRatingStore  # noqa: E402

DB = os.environ.get("DATABASE_URL")


def main() -> int:
    if not DB:
        print("DATABASE_URL 未设置，跳过")
        return 0
    store = PiRatingStore()
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
            if home and away and hg is not None:
                store.update(home, away, hg, ag)
        store.save_to_db(conn)
    print(f"pi-rating 已更新：{len(rows)} 场已结算比赛，"
          f"{len(store.to_state())} 支球队有评分")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
