"""回填球队近期牌数历史：从ESPN summary事件提取每队每场黄/红牌数。

输出 data/cards_history.json: [{team, date, league, yellow, red, venue}]
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "collector", "sources"))
from espn import get_scoreboard, get_summary

LEAGUES = {
    "E0": "英超", "SP1": "西甲", "I1": "意甲", "D1": "德甲", "F1": "法甲",
}
DAYS_BACK = 60
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "cards_history.json")


def parse_cards(summary):
    """从summary事件统计两队黄/红牌。返回 (home_y, home_r, away_y, away_r)。"""
    home, away = summary["home"], summary["away"]
    hy = hr = ay = ar = 0
    for e in summary.get("events", []):
        t = e.get("type", "")
        if "Yellow Card" not in t and "Red Card" not in t:
            continue
        text = e.get("text", "")
        # 文本形如 "Name (Team) is shown the yellow card..."
        m = re.search(r"\(([^)]+)\)", text)
        team = m.group(1) if m else ""
        is_home = team.lower() in home.lower() or home.lower() in team.lower()
        is_away = team.lower() in away.lower() or away.lower() in team.lower()
        if "Yellow Card" in t:
            if is_home: hy += 1
            elif is_away: ay += 1
        else:
            if is_home: hr += 1
            elif is_away: ar += 1
    return hy, hr, ay, ar


def main():
    records = []
    seen = set()
    today = datetime.now().date()
    for code, cn in LEAGUES.items():
        for back in range(1, DAYS_BACK + 1):
            d = today - timedelta(days=back)
            ds = d.strftime("%Y%m%d")
            try:
                sb = get_scoreboard(cn, ds)
            except Exception as e:
                print(f"{cn} {ds} scoreboard失败: {e}", flush=True)
                continue
            for m in sb:
                eid = m.get("event_id")
                if not eid or eid in seen:
                    continue
                if "FT" not in str(m.get("status", "")):
                    continue
                seen.add(eid)
                try:
                    s = get_summary(eid, cn)
                except Exception as e:
                    print(f"  {eid} summary失败: {e}", flush=True)
                    continue
                hy, hr, ay, ar = parse_cards(s)
                date = s.get("date", "")[:10]
                records.append({"team": s["home"], "date": date, "league": code,
                                "yellow": hy, "red": hr, "venue": "H"})
                records.append({"team": s["away"], "date": date, "league": code,
                                "yellow": ay, "red": ar, "venue": "A"})
                time.sleep(0.3)
            print(f"{cn} {ds}: 累计{len(records)}条", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False)
    print(f"写入 {OUT}: {len(records)} 条球队-场次记录")


if __name__ == "__main__":
    main()
