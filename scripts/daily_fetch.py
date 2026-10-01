#!/usr/bin/env python3
"""
每日数据源自动抓取
- API-Football: 当日赛程+比分 (100次/天额度，约用5-10次)
- football-data.org: 当日赛程 (10次/分钟，约用2次)
- The Odds API: 实时赔率快照 (500次/月，约用10-15次)
- football-charts: 历史比分增量 (5000次/天，约用10次)
- theopenmodel: 五大联赛预测 (免费)
- Understat: xG数据 (每周一次，本脚本跳过，由weekly任务处理)

输出: ~/workspace/football-prediction-v2/data/daily/YYYY-MM-DD/
"""

import json
import os
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.expanduser("~/workspace/football-prediction-v2")
SRC_DIR = os.path.join(BASE_DIR, "collector", "sources")
DATA_DIR = os.path.join(BASE_DIR, "data", "daily")
sys.path.insert(0, SRC_DIR)

os.makedirs(DATA_DIR, exist_ok=True)

today = datetime.now().strftime("%Y-%m-%d")
tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
day_dir = os.path.join(DATA_DIR, today)
os.makedirs(day_dir, exist_ok=True)

results = {"date": today, "sources": {}}

def save(name, data):
    path = os.path.join(day_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path

# 1. API-Football: 今日+明日赛程
try:
    from apifootball import get_fixtures, get_status
    fixtures_today = get_fixtures(today)
    fixtures_tmr = get_fixtures(tomorrow)
    save("af_fixtures_today", fixtures_today)
    save("af_fixtures_tomorrow", fixtures_tmr)
    st = get_status()
    results["sources"]["api_football"] = {
        "ok": True,
        "today_count": len(fixtures_today),
        "tomorrow_count": len(fixtures_tmr),
        "quota_used": st["requests_used"],
    }
    print(f"[OK] API-Football: 今日{len(fixtures_today)}场 明日{len(fixtures_tmr)}场")
except Exception as e:
    results["sources"]["api_football"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] API-Football: {e}")

# 2. football-data.org: 今日+明日赛程
try:
    from footballdata import get_matches
    matches = get_matches(today, tomorrow)
    save("fd_matches", matches)
    results["sources"]["football_data_org"] = {"ok": True, "count": len(matches)}
    print(f"[OK] football-data.org: {len(matches)}场")
except Exception as e:
    results["sources"]["football_data_org"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] football-data.org: {e}")

# 3. The Odds API: 五大联赛实时赔率
try:
    from oddsapi import get_odds, get_quota, SOCCER_LEAGUES
    odds_data = {}
    for cn_name, sport_key in [("英超", "soccer_epl"), ("西甲", "soccer_spain_la_liga"),
                               ("意甲", "soccer_italy_serie_a"), ("德甲", "soccer_germany_bundesliga"),
                               ("法甲", "soccer_france_ligue_one")]:
        try:
            odds_data[cn_name] = get_odds(sport_key, markets="h2h", regions="eu")
            print(f"  - {cn_name}: {len(odds_data[cn_name])}场有赔率")
        except Exception as e:
            print(f"  - {cn_name} 失败: {e}")
    save("odds_snapshot", odds_data)
    q = get_quota()
    total_matches = sum(len(v) for v in odds_data.values())
    results["sources"]["the_odds_api"] = {
        "ok": True, "matches": total_matches,
        "quota_used": q["used"], "quota_remaining": q["remaining"],
    }
    print(f"[OK] The Odds API: 共{total_matches}场有赔率，本月已用{q['used']}剩{q['remaining']}")
except Exception as e:
    results["sources"]["the_odds_api"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] The Odds API: {e}")

# 4. theopenmodel: 五大联赛预测
try:
    from openmodel import get_predictions
    preds = get_predictions()
    # 转成可序列化格式
    def _ser(o):
        if isinstance(o, dict):
            return {k: _ser(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_ser(v) for v in o]
        if hasattr(o, 'isoformat'):
            return o.isoformat()
        return o
    preds_clean = _ser(preds) if isinstance(preds, list) else []
    save("openmodel_predictions", preds_clean)
    n = len(preds_clean)
    results["sources"]["theopenmodel"] = {"ok": True, "count": n}
    print(f"[OK] theopenmodel: {n}条预测")
except Exception as e:
    results["sources"]["theopenmodel"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] theopenmodel: {e}")

# 5. football-charts: 历史比分增量 (J2/K1/K2)
try:
    from footballcharts import fetch_league_history
    fc_counts = {}
    for league, code in [("J2", "japan2"), ("K1", "korea1"), ("K2", "korea2")]:
        try:
            history = fetch_league_history(code, seasons=["2026"])
            fc_counts[league] = len(history)
        except Exception as e:
            fc_counts[league] = f"失败: {e}"
    save("fc_history_increment", fc_counts)
    results["sources"]["football_charts"] = {"ok": True, "counts": fc_counts}
    print(f"[OK] football-charts: {fc_counts}")
except Exception as e:
    results["sources"]["football_charts"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] football-charts: {e}")

# 6. ESPN 隐藏 API: 实时比分/赛程 (API-Football备用源，免费无限制)
try:
    from espn import get_scoreboard, LEAGUE_CODES as ESPN_LEAGUES
    espn_data = {}
    for cn_name in ["英超", "西甲", "意甲", "德甲", "法甲", "J1", "巴西甲", "美职"]:
        try:
            sb = get_scoreboard(cn_name)
            espn_data[cn_name] = sb
        except Exception as e:
            print(f"  - ESPN {cn_name} 失败: {e}")
    save("espn_scoreboard", espn_data)
    total = sum(len(v) for v in espn_data.values())
    results["sources"]["espn"] = {"ok": True, "matches": total}
    print(f"[OK] ESPN: 共{total}场")
except Exception as e:
    results["sources"]["espn"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] ESPN: {e}")

# 保存汇总
save("_summary", results)
print(f"\n汇总已保存到 {day_dir}/_summary.json")
ok_count = sum(1 for s in results["sources"].values() if s.get("ok"))
print(f"成功 {ok_count}/{len(results['sources'])} 个源")
