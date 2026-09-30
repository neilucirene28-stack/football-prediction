#!/usr/bin/env python3
"""构建红黄牌历史数据：从 football-data.co.uk 拉免费 CSV，
生成 engine/cards_history.json（联赛基线 + 球队主客场吃牌/造牌 + 裁判统计）。

只存原始计数，收缩（shrinkage）在 engine/cards.py 预测时做。
注意：football-data 只有英超 (E0) 带 Referee 列，其他联赛裁判统计缺失。
"""
import csv
import glob
import json
import os
import urllib.request

SEASONS = ["2122", "2223", "2324", "2425"]
LEAGUES = {"E0": "英超", "SP1": "西甲", "I1": "意甲", "D1": "德甲", "F1": "法甲"}
CSV_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cards_csv")
OUT = os.path.join(os.path.dirname(__file__), "..", "engine",
                   "cards_history.json")


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _i(x, default=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def download():
    os.makedirs(CSV_DIR, exist_ok=True)
    for s in SEASONS:
        for code in LEAGUES:
            p = os.path.join(CSV_DIR, f"{s}_{code}.csv")
            if os.path.exists(p):
                continue
            url = f"https://www.football-data.co.uk/mmz4281/{s}/{code}.csv"
            print("下载", url)
            urllib.request.urlretrieve(url, p)


def _team(d, name):
    return d.setdefault(name, {"home": {"mp": 0, "yf": 0, "ya": 0,
                                        "rf": 0, "ra": 0},
                               "away": {"mp": 0, "yf": 0, "ya": 0,
                                        "rf": 0, "ra": 0}})


def build():
    leagues = {}
    for code, cname in LEAGUES.items():
        L = {"name": cname, "matches": 0,
             "home_yellow": 0.0, "away_yellow": 0.0,
             "home_red": 0.0, "away_red": 0.0,
             "teams": {}, "referees": {}}
        teams = L["teams"]
        for s in SEASONS:
            for f in glob.glob(os.path.join(CSV_DIR, f"{s}_{code}.csv")):
                with open(f, encoding="utf-8-sig") as fh:
                    for x in csv.DictReader(fh):
                        hy, ay = _i(x.get("HY")), _i(x.get("AY"))
                        hr, ar = _i(x.get("HR")), _i(x.get("AR"))
                        ht, at = x.get("HomeTeam"), x.get("AwayTeam")
                        if not ht or not at:
                            continue
                        L["matches"] += 1
                        L["home_yellow"] += hy
                        L["away_yellow"] += ay
                        L["home_red"] += hr
                        L["away_red"] += ar
                        h, a = _team(teams, ht), _team(teams, at)
                        h["home"]["mp"] += 1
                        h["home"]["yf"] += hy
                        h["home"]["ya"] += ay   # 对手吃牌 = 本队造牌
                        h["home"]["rf"] += hr
                        h["home"]["ra"] += ar
                        a["away"]["mp"] += 1
                        a["away"]["yf"] += ay
                        a["away"]["ya"] += hy
                        a["away"]["rf"] += ar
                        a["away"]["ra"] += hr
                        ref = (x.get("Referee") or "").strip()
                        if ref:
                            r = L["referees"].setdefault(
                                ref, {"mp": 0, "cards": 0, "reds": 0})
                            r["mp"] += 1
                            r["cards"] += hy + ay
                            r["reds"] += hr + ar
        m = max(L["matches"], 1)
        for k in ("home_yellow", "away_yellow", "home_red", "away_red"):
            L[k] = round(L[k] / m, 4)
        leagues[code] = L
    return {"seasons": SEASONS,
            "leagues": leagues,
            "note": "仅英超有裁判数据；其他联赛裁判统计缺失，预测时乘子=1.0"}


def main() -> int:
    download()
    data = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    for code, L in data["leagues"].items():
        print(f"{L['name']}: {L['matches']}场 主黄{L['home_yellow']} "
              f"客黄{L['away_yellow']} 主红{L['home_red']} 客红{L['away_red']} "
              f"球队{len(L['teams'])} 裁判{len(L['referees'])}")
    print("已写入", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
