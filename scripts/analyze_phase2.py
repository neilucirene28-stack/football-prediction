#!/usr/bin/env python3
"""Phase 2 分析：让平低估 / 背离规则 / 赛区分异，附样本量与标准误。

口径（与 Phase 1 校正后一致）：
  adjusted_margin = (hg-ag) + rq；rq<0 主让，rq>0 主受让；让平 ⟺ ==0
  模型让平概率 = handicap_1x2(score_matrix(lam_h, lam_a, rho=-0.13), line=rq)[1]
"""
import json, math, sys
from collections import defaultdict

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.poisson import score_matrix, handicap_1x2

BASE = "/home/hatch/workspace/football-prediction-v2"
recs = [json.loads(l) for l in open(f"{BASE}/hidden_backfill_phase2.jsonl")]
done = [r for r in recs if r["actual"] in ("H", "D", "A")]
print(f"预测 {len(recs)} 场，可结算 {len(done)} 场（1324238 无比分未计）")


def se(p, n):
    return math.sqrt(p * (1 - p) / n) if n else float("nan")


def brier(ps, actuals):
    n = len(ps)
    return sum((p[i] - (1 if "HDA"[i] == a else 0)) ** 2
               for p, a in zip(ps, actuals) for i in range(3)) / n


def model_hdc(r):
    """模型让球胜平负概率。"""
    lh, la = r["lam"]
    m = score_matrix(lh, la, rho=-0.13)
    return handicap_1x2(m, r["jc_rq"])


def adj_margin(r):
    hg, ag = (int(x) for x in r["score"].split("-"))
    return (hg - ag) + r["jc_rq"]


with_rq = [r for r in done if r["jc_rq"] not in (None, 0)]
print(f"\n==== 1. 让平：实际 vs 模型（n={len(with_rq)}）====")
n = len(with_rq)
act = sum(1 for r in with_rq if adj_margin(r) == 0)
mod = sum(model_hdc(r)[1] for r in with_rq) / n
print(f"实际让平 {act}/{n}={act/n:.1%}  SE={se(act/n, n):.1%}")
print(f"模型让平均值 {mod:.1%}  差值 {act/n-mod:+.1%}")
for q in (1, 2, 3):
    sub = [r for r in with_rq if abs(r["jc_rq"]) == q]
    if len(sub) < 5:
        print(f"  |rq|={q}: n={len(sub)} 太小，略"); continue
    a = sum(1 for r in sub if adj_margin(r) == 0) / len(sub)
    m = sum(model_hdc(r)[1] for r in sub) / len(sub)
    print(f"  |rq|={q}: n={len(sub)} 实际 {a:.1%} (SE {se(a,len(sub)):.1%}) 模型 {m:.1%} 差 {a-m:+.1%}")

print("\n==== 2. 方向命中 / Brier ====")
for name, getp in (("融合", lambda r: r["p"]),
                   ("纯模型", lambda r: r["signals"]["model"]),
                   ("纯市场", lambda r: r["signals"]["market"])):
    sub = [r for r in done if getp(r)]
    hit = sum(1 for r in sub if "HDA"[getp(r).index(max(getp(r)))] == r["actual"])
    br = brier([getp(r) for r in sub], [r["actual"] for r in sub])
    print(f"{name}: n={len(sub)} 方向 {hit}/{len(sub)}={hit/len(sub):.1%} (SE {se(hit/len(sub),len(sub)):.1%}) Brier={br:.4f}")

print("\n==== 3. 严重背离（gap>=0.15 且方向不一致）：谁占优 ====")
div = [r for r in done if r.get("divergence") and r["signals"]["market"]]
print(f"背离场 n={len(div)}")
mw = sum(1 for r in div if {"home": "H", "draw": "D", "away": "A"}[r["divergence"]["market_direction"]] == r["actual"])
md = sum(1 for r in div if {"home": "H", "draw": "D", "away": "A"}[r["divergence"]["model_direction"]] == r["actual"])
print(f"  市场方向命中 {mw}/{len(div)}={mw/len(div):.1%} (SE {se(mw/len(div),len(div)):.1%})")
print(f"  模型方向命中 {md}/{len(div)}={md/len(div):.1%} (SE {se(md/len(div),len(div)):.1%})")

print("\n==== 4. 分赛区（可结算场） ====")
groups = defaultdict(list)
for r in done:
    lg = r["league"] or ""
    if "J2" in lg or "日乙" in lg: g = "J2"
    elif lg.startswith("J") or "日职" in lg or "J联赛" in lg: g = "J1"
    elif "K联" in lg or "韩K" in lg or "韩国" in lg: g = "韩K"
    elif "美职" in lg or "MLS" in lg: g = "美职"
    elif "欧冠" in lg or "欧联" in lg or "欧协" in lg: g = "欧战"
    elif "亚运" in lg or "U23" in lg or "国奥" in lg: g = "亚运/U23"
    elif "友谊" in lg or "世界杯" in lg or "欧国联" in lg or "世预" in lg: g = "国家队"
    elif "巴西" in lg or "巴甲" in lg: g = "巴甲"
    elif "沙特" in lg: g = "沙特"
    else: g = "其他"
    groups[g].append(r)
for g in sorted(groups, key=lambda x: -len(groups[x])):
    sub = groups[g]
    hit = sum(1 for r in sub if "HDA"[r["p"].index(max(r["p"]))] == r["actual"])
    br = brier([r["p"] for r in sub], [r["actual"] for r in sub])
    rqsub = [r for r in sub if r["jc_rq"] not in (None, 0)]
    hd = f"{sum(1 for r in rqsub if adj_margin(r)==0)}/{len(rqsub)}" if rqsub else "-"
    print(f"  {g}: n={len(sub)} 方向{hit/len(sub):.1%} Brier={br:.4f} 让平实际 {hd}")

print("\n==== 5. 总进球偏差 ====")
errs = []
for r in done:
    hg, ag = (int(x) for x in r["score"].split("-"))
    eg = r["expected_goals"]
    if eg: errs.append((hg + ag) - eg)
if errs:
    import statistics
    print(f"n={len(errs)} 偏差均值 {statistics.mean(errs):+.2f}  MAE {sum(abs(e) for e in errs)/len(errs):.2f}")
