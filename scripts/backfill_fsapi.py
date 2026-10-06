#!/usr/bin/env python3
"""FsAPI 历史回填驱动：一次拉一个联赛赛季，配额安全。

免费 key 50/天，拉取分散到多天执行。本脚本只做比分档案回填
（walk-forward 用），不进实时预测。

用法:
  python3 scripts/backfill_fsapi.py --league-id lg_XXXX --season 2024
  python3 scripts/backfill_fsapi.py --league-id lg_XXXX --season 2024 --dry-run  # 只看配额
"""
import sys, os, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "collector", "sources"))

from footballsoccerapi import fetch_season, quota_used, DAILY_CAP


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-id", required=True)
    ap.add_argument("--season", required=True, type=int)
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args()

    print(f"今日配额已用: {quota_used()}/{DAILY_CAP}")
    if ns.dry_run:
        return 0

    out = os.path.expanduser(
        f"~/workspace/football-prediction-v2/data/backfill_fsapi/"
        f"{ns.league_id}_{ns.season}.json")
    res = fetch_season(ns.league_id, ns.season, out_path=out)
    print(f"回填完成: {res['matches']} 场，{res['pages']} 页 → {res['out']}")
    print(f"今日配额已用: {res['quota_used_today']}/{DAILY_CAP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
