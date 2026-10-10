#!/usr/bin/env python3
"""竞彩30天回填：澳客赛程(194场FD覆盖) + football-data.co.uk 数据，严格无泄漏。

- 比赛清单：/tmp/jc_fd.json（澳客 /jingcai/ 30天，FD可覆盖）
- 数据：FD 2627（目标）+ 2526（burn-in），比分/赔率/战绩
- 市场：FD closing (AvgCH/CD/CA) 为主；竞彩 odds/rq 存 enrichment
- 无泄漏：team_history/h2h/league_avg 严格 date<D；Elo 按日期分组批量更新
- 走生产 v2.4 predict() 同一条链路
"""
import csv, glob, json, sys, math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
TZ = timezone(timedelta(hours=8))

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
sys.path.insert(0, "/tmp")
from engine.jingcai_predictor import predict, PredictError
from engine.elo import update_ratings
from cn2en_jc import CN2EN

FD_DIR = "/tmp/fd2627"
JC = "/tmp/jc_fd.json"
OUT = "/home/hatch/workspace/football-prediction-v2/hidden_backfill_jingcai.jsonl"
LEAGUE_CN = {"E0": "英超", "E1": "英冠", "SP1": "西甲", "I1": "意甲", "D1": "德甲",
             "F1": "法甲", "N1": "荷甲", "P1": "葡超", "D2": "德乙", "F2": "法乙"}


def load_fd():
    rows2627, rows2526 = [], []
    for f in glob.glob(f"{FD_DIR}/*.csv"):
        is2526 = "_2526" in f
        lg = f.split("/")[-1].replace("_2526.csv", "").replace(".csv", "")
        for row in csv.DictReader(open(f, encoding="latin1")):
            d = (row.get("Date") or "").strip()
            h, a = (row.get("HomeTeam") or "").strip(), (row.get("AwayTeam") or "").strip()
            if not (d and h and a):
                continue
            try:
                dt = datetime.strptime(d, "%d/%m/%Y").date()
            except ValueError:
                continue
            def num(x):
                try: return float(x) if x not in (None, "") else None
                except (ValueError, TypeError): return None
            r = {"date": dt, "league": lg, "home": h, "away": a,
                 "fthg": num(row.get("FTHG")), "ftag": num(row.get("FTAG")),
                 "ftr": (row.get("FTR") or "").strip(),
                 "hthg": num(row.get("HTHG")), "htag": num(row.get("HTAG")),
                 "htr": (row.get("HTR") or "").strip(),
                 "ch": num(row.get("AvgCH")), "cd": num(row.get("AvgCD")), "ca": num(row.get("AvgCA")),
                 "oh": num(row.get("AvgH")), "od": num(row.get("AvgD")), "oa": num(row.get("AvgA")),
                 "ah_line": num(row.get("AHh")),
                 "ah_h": num(row.get("AvgAHH")), "ah_a": num(row.get("AvgAHA")),
                 }
            (rows2526 if is2526 else rows2627).append(r)
    return rows2627, rows2526


def team_history(rows, team, before, n=10):
    out = []
    for r in sorted([x for x in rows if x["date"] < before
                     and (x["home"] == team or x["away"] == team)],
                    key=lambda x: x["date"], reverse=True)[:n]:
        is_home = r["home"] == team
        gf = r["fthg"] if is_home else r["ftag"]
        ga = r["ftag"] if is_home else r["fthg"]
        if gf is None: continue
        ftr = r["ftr"]
        res = "W" if (ftr == "H") == is_home else ("D" if ftr == "D" else "L")
        out.append({"opponent": r["away"] if is_home else r["home"],
                    "home_away": "H" if is_home else "A",
                    "gf": int(gf), "ga": int(ga), "result": res,
                    "date": r["date"].isoformat()})
    return out


def h2h(rows, h, a, before, n=6):
    """h2h 以目标主队 h 视角：gf=h进球。"""
    out = []
    for r in sorted([x for x in rows if x["date"] < before and
                     ((x["home"] == h and x["away"] == a) or
                      (x["home"] == a and x["away"] == h))],
                    key=lambda x: x["date"], reverse=True)[:n]:
        if r["fthg"] is None: continue
        if r["home"] == h:
            gf, ga = int(r["fthg"]), int(r["ftag"])
        else:
            gf, ga = int(r["ftag"]), int(r["fthg"])
        out.append({"date": r["date"].isoformat(), "gf": gf, "ga": ga,
                    "score": f"{int(r['fthg'])}-{int(r['ftag'])}"})
    return out


def league_avg(rows, lg, before):
    gs = [r["fthg"] + r["ftag"] for r in rows
          if r["league"] == lg and r["date"] < before
          and r["fthg"] is not None]
    return sum(gs) / len(gs) if gs else 2.7


def devig(o):
    inv = sum(1 / x for x in o)
    return [x * inv for x in o]


def main():
    rows2627, rows2526 = load_fd()
    allrows = rows2627 + rows2526
    # FD 索引：(date, home, away) -> row
    fidx = {(r["date"].isoformat(), r["home"], r["away"]): r for r in rows2627}
    jc = json.load(open(JC))
    # 排除缺失场
    jc = [r for r in jc if not (r["date"] == "2026-09-16" and r["home"] == "莱万特")]
    print(f"竞彩清单 {len(jc)} 场", flush=True)

    elo = defaultdict(lambda: 1500.0)
    # burn-in Elo（2526全量，按日期分组）
    for d in sorted(set(r["date"] for r in rows2526)):
        for r in [x for x in rows2526 if x["date"] == d and x["ftr"] in ("H", "D", "A")]:
            elo[r["home"]], elo[r["away"]] = update_ratings(
                elo[r["home"]], elo[r["away"]], r["ftr"])
    # 2627中早于9-1的也burn-in
    d0 = datetime(2026, 9, 1).date()
    for d in sorted(set(r["date"] for r in rows2627 if r["date"] < d0)):
        for r in [x for x in rows2627 if x["date"] == d and x["ftr"] in ("H", "D", "A")]:
            elo[r["home"]], elo[r["away"]] = update_ratings(
                elo[r["home"]], elo[r["away"]], r["ftr"])

    results, skipped = [], 0
    # 按日期分组（修同日泄漏：当天预测完再批量更新Elo）
    bydate = defaultdict(list)
    for r in jc:
        bydate[r["date"]].append(r)
    for dstr in sorted(bydate):
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
        day_matches = []
        for j in bydate[dstr]:
            h_en, a_en = CN2EN[j["home"]], CN2EN[j["away"]]
            fr = fidx.get((dstr, h_en, a_en))
            if not fr or fr["ftr"] not in ("H", "D", "A"):
                skipped += 1; continue
            ko = datetime(d.year, d.month, d.day, 15, 0, tzinfo=TZ)  # 占位
            hr = team_history(allrows, h_en, d)
            ar = team_history(allrows, a_en, d)
            if not hr or not ar:
                skipped += 1; continue
            # 市场：FD closing 为主
            odds = None
            if fr["ch"] and fr["cd"] and fr["ca"]:
                odds = {"home": fr["ch"], "draw": fr["cd"], "away": fr["ca"]}
            opening = None
            if fr["oh"] and fr["od"] and fr["oa"]:
                opening = {"home": fr["oh"], "draw": fr["od"], "away": fr["oa"]}
            asian = None
            if fr["ah_line"] not in (None, ""):
                try: asian = {"handicap": float(fr["ah_line"])}
                except (ValueError, TypeError): pass
            payload = {
                "home": h_en, "away": a_en,
                "league": LEAGUE_CN.get(fr["league"], fr["league"]),
                "kickoff_at": ko.isoformat(),
                "snapshot_at": (ko - timedelta(hours=3)).isoformat(),
                "home_recent": hr, "away_recent": ar,
                "h2h": h2h(allrows, h_en, a_en, d),
                "league_avg_goals": round(league_avg(allrows, fr["league"], d), 3),
                "odds": odds, "opening_odds": opening,
                "asian": asian, "ou_line": 2.5,
                "elo": {"home": round(elo[h_en], 1), "away": round(elo[a_en], 1)},
            }
            try:
                res = predict(payload, None, asof=ko - timedelta(hours=1))
            except PredictError:
                skipped += 1; continue
            if res.get("status") == "insufficient_data":
                skipped += 1; continue
            # 竞彩让球
            try: rq = int(j["rq"]) if j["rq"] not in (None, "") else 0
            except (ValueError, TypeError): rq = 0
            jcodds = [float(x) for x in j.get("odds", [])] if j.get("odds") else None
            rec = {
                "date": dstr, "jc_no": j["no"], "jc_league": j["league"],
                "home": h_en, "away": a_en, "home_cn": j["home"], "away_cn": j["away"],
                "actual": fr["ftr"], "score": f"{int(fr['fthg'])}-{int(fr['ftag'])}",
                "htr": fr["htr"], "ht_score": f"{int(fr['hthg'] or 0)}-{int(fr['htag'] or 0)}",
                "p": [res["p_home"], res["p_draw"], res["p_away"]],
                "signals": res.get("signals"), "weights": res.get("weights"),
                "divergence": res.get("divergence"), "grade": res.get("grade"),
                "completeness": res.get("completeness"),
                "top_score": (res.get("derivatives", {}) or {}).get("top_scores", [{}])[0],
                "expected_goals": (res.get("derivatives", {}) or {}).get("expected_goals"),
                "asian_pred": (res.get("derivatives", {}) or {}).get("asian"),
                "ou_pred": (res.get("derivatives", {}) or {}).get("over_under"),
                "half_time": (res.get("derivatives", {}) or {}).get("half_time"),
                "lam": [res.get("lambda_home"), res.get("lambda_away")],
                "jc_odds": jcodds, "jc_rq": rq,
                "fd_closing": [fr["ch"], fr["cd"], fr["ca"]],
                "fd_opening": [fr["oh"], fr["od"], fr["oa"]],
                "fd_ah_line": fr["ah_line"],
                "elo_pre": [round(elo[h_en], 1), round(elo[a_en], 1)],
            }
            results.append(rec)
            day_matches.append(fr)
        # 当天结束后批量更新 Elo（无同日泄漏）
        for fr in day_matches:
            elo[fr["home"]], elo[fr["away"]] = update_ratings(
                elo[fr["home"]], elo[fr["away"]], fr["ftr"])
        print(f"  {dstr}: 累计 {len(results)}", flush=True)

    with open(OUT, "w") as fh:
        for rec in results:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"完成 {len(results)} 场，跳过 {skipped} 场 → {OUT}", flush=True)


if __name__ == "__main__":
    main()
