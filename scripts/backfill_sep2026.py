#!/usr/bin/env python3
"""2026-09 竞彩核心联赛回填复盘（严格无泄漏 walk-forward）。

数据：football-data.co.uk 2627 赛季 E0/SP1/D1/I1/F1（五大联赛 = 竞彩主体）。
  - 近期战绩：只用开球日期严格之前 (date < D) 的比赛
  - 市场信号：收盘赔率 AvgCH/AvgCD/AvgCA（赛前已定，无泄漏）
  - 初盘：AvgH/AvgD/AvgA（周五/周二采集的 pre-closing，用于 drift）
  - Elo：2526 整赛季 burn-in + 2627 按时间顺序更新，目标场次用赛前评分
  - predict(..., asof=kickoff-1h) 走生产同一条 v2.4 链路

输出：per-match JSONL + 终端聚合报告（校准指标 / 背离复盘 / 亚盘让平 / 进球偏差）。
"""
import sys, os, csv, json, math
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.predictor import predict, PredictError
from engine.elo import update_ratings

TZ8 = timezone(timedelta(hours=8))
DATA_DIR = "/tmp/fd2627"
LEAGUES = ["E0", "SP1", "D1", "I1", "F1"]
LEAGUE_CN = {"E0": "英超", "SP1": "西甲", "D1": "德甲", "I1": "意甲", "F1": "法甲"}
START, END = date(2026, 9, 1), date(2026, 9, 29)
OUT_JSONL = "/home/hatch/workspace/football-prediction-v2/hidden_backfill_sep2026.jsonl"


def parse_date(s):
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def fnum(s):
    try:
        v = float((s or "").strip())
        return v if v > 0 else None
    except (ValueError, AttributeError):
        return None


def load_league(code, season):
    path = os.path.join(DATA_DIR, f"{code}_{season}.csv")
    if not os.path.exists(path):
        path = os.path.join(DATA_DIR, f"{code}.csv") if season == "2627" else None
    rows = []
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            d = parse_date(r.get("Date"))
            if not d or not r.get("HomeTeam") or not r.get("AwayTeam"):
                continue
            rows.append({
                "date": d, "league": code,
                "home": r["HomeTeam"].strip(), "away": r["AwayTeam"].strip(),
                "fthg": r.get("FTHG"), "ftag": r.get("FTAG"), "ftr": (r.get("FTR") or "").strip(),
                "hthg": r.get("HTHG"), "htag": r.get("HTAG"), "htr": (r.get("HTR") or "").strip(),
                "ch": fnum(r.get("AvgCH")), "cd": fnum(r.get("AvgCD")), "ca": fnum(r.get("AvgCA")),
                "oh": fnum(r.get("AvgH")), "od": fnum(r.get("AvgD")), "oa": fnum(r.get("AvgA")),
                "ah_close": r.get("AHCh"), "ah_open": r.get("AHh"),
            })
    return rows


def ensure_2526():
    for code in LEAGUES:
        p = os.path.join(DATA_DIR, f"{code}_2526.csv")
        if not os.path.exists(p) or os.path.getsize(p) < 1000:
            import urllib.request
            url = f"https://www.football-data.co.uk/mmz4281/2526/{code}.csv"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as rh, open(p, "wb") as fh:
                fh.write(rh.read())
            print(f"下载 2526/{code}: {os.path.getsize(p)} bytes", flush=True)


def team_history(all_rows, team, before):
    """before 之前该队比赛（严格 <），按新→旧，最多10场。返回 predictor 战绩 dict 列表。"""
    out = []
    for r in all_rows:
        if r["date"] >= before:
            continue
        if r["home"] == team:
            if r["ftr"] not in ("H", "D", "A"):
                continue
            out.append({"venue": "H", "gf": int(r["fthg"]), "ga": int(r["ftag"]),
                        "comp": r["league"], "date": r["date"].isoformat()})
        elif r["away"] == team:
            if r["ftr"] not in ("H", "D", "A"):
                continue
            out.append({"venue": "A", "gf": int(r["ftag"]), "ga": int(r["fthg"]),
                        "comp": r["league"], "date": r["date"].isoformat()})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:10]


def h2h(all_rows, home, away, before):
    out = []
    for r in all_rows:
        if r["date"] >= before:
            continue
        if {r["home"], r["away"]} == {home, away} and r["ftr"] in ("H", "D", "A"):
            if r["home"] == home:
                out.append({"gf": int(r["fthg"]), "ga": int(r["ftag"])})
            else:
                out.append({"gf": int(r["ftag"]), "ga": int(r["fthg"])})
    return out[-5:]


def league_avg(all_rows, league, before):
    tot, n = 0, 0
    for r in all_rows:
        if r["league"] == league and r["date"] < before and r["ftr"] in ("H", "D", "A"):
            tot += int(r["fthg"]) + int(r["ftag"])
            n += 1
    return (tot / n) if n >= 10 else 2.70


def main():
    ensure_2526()
    rows2627, rows2526 = [], []
    for code in LEAGUES:
        rows2627 += load_league(code, "2627")
        rows2526 += load_league(code, "2526")
    print(f"2627: {len(rows2627)} 场；2526: {len(rows2526)} 场", flush=True)

    # ---- Elo：2526 burn-in + 2627 按时间推进 ----
    elo = defaultdict(lambda: 1500.0)
    for r in sorted(rows2526, key=lambda x: x["date"]):
        if r["ftr"] in ("H", "D", "A"):
            elo[r["home"]], elo[r["away"]] = update_ratings(
                elo[r["home"]], elo[r["away"]], r["ftr"])

    targets = [r for r in rows2627
               if START <= r["date"] <= END and r["ftr"] in ("H", "D", "A")]
    targets.sort(key=lambda x: x["date"])
    print(f"目标场次（9/1–9/29 已完赛）: {len(targets)}", flush=True)

    # 2627 非目标比赛也按顺序喂给 Elo（保持时间推进严格性）
    rest2627 = sorted([r for r in rows2627 if r not in targets],
                      key=lambda x: x["date"])
    feed = sorted(targets + [r for r in rest2627 if r["date"] < START],
                  key=lambda x: x["date"])

    results, skipped = [], 0
    for r in feed:
        d = r["date"]
        hr = team_history(rows2627 + rows2526, r["home"], d)
        ar = team_history(rows2627 + rows2526, r["away"], d)
        is_target = r in targets
        rec = None
        if is_target:
            ko = datetime(d.year, d.month, d.day, 15, 0, tzinfo=TZ8)
            odds = None
            if r["ch"] and r["cd"] and r["ca"]:
                odds = {"home": r["ch"], "draw": r["cd"], "away": r["ca"]}
            opening = None
            if r["oh"] and r["od"] and r["oa"]:
                opening = {"home": r["oh"], "draw": r["od"], "away": r["oa"]}
            asian = None
            try:
                if r["ah_close"] not in (None, ""):
                    asian = {"handicap": float(r["ah_close"])}
                    if r["ah_open"] not in (None, ""):
                        asian["opening_handicap"] = float(r["ah_open"])
            except (ValueError, TypeError):
                asian = None
            payload = {
                "home": r["home"], "away": r["away"],
                "league": LEAGUE_CN[r["league"]],
                "kickoff_at": ko.isoformat(),
                "snapshot_at": (ko - timedelta(hours=3)).isoformat(),
                "home_recent": hr, "away_recent": ar,
                "h2h": h2h(rows2627 + rows2526, r["home"], r["away"], d),
                "league_avg_goals": round(league_avg(rows2627 + rows2526, r["league"], d), 3),
                "odds": odds, "opening_odds": opening,
                "asian": asian, "ou_line": 2.5,
                "elo": {"home": round(elo[r["home"]], 1),
                        "away": round(elo[r["away"]], 1)},
            }
            try:
                res = predict(payload, None, asof=ko - timedelta(hours=1))
            except PredictError as e:
                skipped += 1
                rec = None
            else:
                if res.get("status") == "insufficient_data":
                    skipped += 1
                else:
                    rec = {
                        "date": d.isoformat(), "league": r["league"],
                        "home": r["home"], "away": r["away"],
                        "actual": r["ftr"], "score": f"{r['fthg']}-{r['ftag']}",
                        "htr": r["htr"],
                        "p": [res["p_home"], res["p_draw"], res["p_away"]],
                        "signals": res.get("signals"),
                        "weights": res.get("weights"),
                        "divergence": res.get("divergence"),
                        "grade": res.get("grade"),
                        "completeness": res.get("completeness"),
                        "top_score": (res.get("derivatives", {}) or {}).get("top_scores", [{}])[0],
                        "expected_goals": ((res.get("derivatives", {}) or {})
                                           .get("expected_goals")),
                        "asian_pred": ((res.get("derivatives", {}) or {}).get("asian")),
                        "ou_pred": ((res.get("derivatives", {}) or {}).get("over_under")),
                        "half_time": ((res.get("derivatives", {}) or {}).get("half_time")),
                        "lam": [res.get("lambda_home"), res.get("lambda_away")],
                        "ah_close": r["ah_close"],
                        "elo_pre": [round(elo[r["home"]], 1), round(elo[r["away"]], 1)],
                    }
                    results.append(rec)
        # 推进 Elo（所有已完赛场次，含目标场）
        if r["ftr"] in ("H", "D", "A"):
            elo[r["home"]], elo[r["away"]] = update_ratings(
                elo[r["home"]], elo[r["away"]], r["ftr"])

    with open(OUT_JSONL, "w") as fh:
        for rec in results:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"完成 {len(results)} 场，跳过 {skipped} 场 → {OUT_JSONL}", flush=True)


if __name__ == "__main__":
    main()
