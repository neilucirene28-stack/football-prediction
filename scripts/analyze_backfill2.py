#!/usr/bin/env python3
"""第二轮：整数盘口走水率拆分 + 初盘→收盘漂移（资金流向代理）× 背离复盘。"""
import sys, json, csv, math
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
IN = "/home/hatch/workspace/football-prediction-v2/hidden_backfill_sep2026.jsonl"
DATA_DIR = "/tmp/fd2627"
IDX = {"H": 0, "D": 1, "A": 2}
NAMES = ["home", "draw", "away"]


def parse_date(s):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def fnum(s):
    try:
        v = float((s or "").strip())
        return v if v > 0 else None
    except (ValueError, AttributeError):
        return None


# 重读 CSV 拿初盘/收盘赔率（漂移用）
odds = {}
for code in ["E0", "SP1", "D1", "I1", "F1"]:
    with open(f"{DATA_DIR}/{code}.csv", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            d = parse_date(r.get("Date") or "")
            if not d:
                continue
            odds[(code, d.isoformat(), r["HomeTeam"].strip(), r["AwayTeam"].strip())] = {
                "oh": fnum(r.get("AvgH")), "od": fnum(r.get("AvgD")), "oa": fnum(r.get("AvgA")),
                "ch": fnum(r.get("AvgCH")), "cd": fnum(r.get("AvgCD")), "ca": fnum(r.get("AvgCA")),
            }


def implied(h, d, a):
    s = 1/h + 1/d + 1/a
    return [1/h/s, 1/d/s, 1/a/s]


recs = [json.loads(l) for l in open(IN)]

# ---------- A. 整数盘口走水率拆分 ----------
print("== A. 亚盘走水率：整数盘 vs 非整数盘 ==")
for label, filt in [("整数盘（-1/-2…）", lambda x: abs(float(x) - round(float(x))) < 1e-9),
                    ("非整数盘", lambda x: abs(float(x) - round(float(x))) >= 1e-9)]:
    sub = [r for r in recs if r["asian_pred"] and r["ah_close"] not in (None, "")
           and filt(r["ah_close"])]
    if not sub:
        print(f"  {label}: 无样本"); continue
    push_n = 0
    pp = []
    for r in sub:
        hg, ag = map(int, r["score"].split("-"))
        margin = hg - ag + float(r["ah_close"])
        if margin == 0:
            push_n += 1
        pp.append(r["asian_pred"]["push"])
    print(f"  {label}: n={len(sub)}，实际走水 {push_n} 场（{push_n/len(sub):.1%}），"
          f"模型平均 push 概率 {sum(pp)/len(pp):.1%}")

# ---------- B. 漂移（资金流向代理） ----------
print("\n== B. 初盘→收盘漂移 vs 赛果 ==")
drift_rows = []
for r in recs:
    o = odds.get((r["league"], r["date"], r["home"], r["away"]))
    if not o or not all([o["oh"], o["od"], o["oa"], o["ch"], o["cd"], o["ca"]]):
        continue
    po = implied(o["oh"], o["od"], o["oa"])
    pc = implied(o["ch"], o["cd"], o["ca"])
    fav = pc.index(max(pc))
    drift = pc[fav] - po[fav]  # 收盘相对初盘，热门方向的概率变化
    hit = (IDX[r["actual"]] == fav)
    drift_rows.append((drift, hit, r))
print(f"  有初盘/收盘样本 {len(drift_rows)} 场")
for label, filt in [("热门被追捧 drift>0", lambda d: d > 0.005),
                    ("基本不动", lambda d: abs(d) <= 0.005),
                    ("热门被抛弃 drift<0", lambda d: d < -0.005)]:
    sub = [(d, h) for d, h, _ in drift_rows if filt(d)]
    if sub:
        hr = sum(h for _, h in sub) / len(sub)
        print(f"  {label}: n={len(sub)}，热门打出率 {hr:.1%}")

# ---------- C. 背离 × 漂移：资金站在哪边，谁赢 ----------
print("\n== C. 背离场次：漂移方向 vs 胜者 ==")
divs = [r for r in recs if r["divergence"]]
for r in divs:
    o = odds.get((r["league"], r["date"], r["home"], r["away"]))
    d = r["divergence"]
    m_dir, k_dir = d["model_direction"], d["market_direction"]
    actual_dir = NAMES[IDX[r["actual"]]]
    winner = "市场" if actual_dir == k_dir else ("模型" if actual_dir == m_dir else "都没中")
    flow = "?"
    if o and all([o["oh"], o["od"], o["oa"], o["ch"], o["cd"], o["ca"]]):
        po = implied(o["oh"], o["od"], o["oa"])
        pc = implied(o["ch"], o["cd"], o["ca"])
        mi, ki = NAMES.index(m_dir), NAMES.index(k_dir)
        dm, dk = pc[mi] - po[mi], pc[ki] - po[ki]
        flow = ("模型" if dm > dk + 0.005 else ("市场" if dk > dm + 0.005 else "中性"))
    print(f"  [{winner}赢/资金→{flow}] {r['date'][5:]} {r['league']} "
          f"{r['home'][:12]:<12} {r['score']:>4} 模型{m_dir} 市场{k_dir}")
# 汇总
cnt = defaultdict(int)
for r in divs:
    o = odds.get((r["league"], r["date"], r["home"], r["away"]))
    d = r["divergence"]
    actual_dir = NAMES[IDX[r["actual"]]]
    winner = "market" if actual_dir == d["market_direction"] else \
             ("model" if actual_dir == d["model_direction"] else "none")
    flow = "?"
    if o and all([o["oh"], o["od"], o["oa"], o["ch"], o["cd"], o["ca"]]):
        po = implied(o["oh"], o["od"], o["oa"])
        pc = implied(o["ch"], o["cd"], o["ca"])
        mi, ki = NAMES.index(d["model_direction"]), NAMES.index(d["market_direction"])
        dm, dk = pc[mi] - po[mi], pc[ki] - po[ki]
        flow = "model" if dm > dk + 0.005 else ("market" if dk > dm + 0.005 else "neutral")
    cnt[(flow, winner)] += 1
print("\n  汇总（资金流向 × 胜者）：")
for fl in ["market", "model", "neutral", "?"]:
    row = {w: cnt[(fl, w)] for w in ["market", "model", "none"]}
    t = sum(row.values())
    if t:
        print(f"    资金→{fl}: n={t}，市场赢{row['market']} 模型赢{row['model']} 都没中{row['none']}")
