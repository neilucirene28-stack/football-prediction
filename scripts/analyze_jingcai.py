#!/usr/bin/env python3
"""竞彩194场回填分析：校准/背离/漂移/让球/权重。"""
import json, math, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collections import defaultdict

REC = ROOT / "hidden_backfill_jingcai.jsonl"
recs = [json.loads(l) for l in open(REC)]
print(f"样本 {len(recs)} 场（{min(r['date'] for r in recs)}~{max(r['date'] for r in recs)}）")
print("口径：Brier为三项平方误差求和；让球D节仅为raw DC重建，不代表当前IPF+letdraw生产链路。")
print("完整分阶段审计请运行 scripts/audit_jingcai_letdraw.py；旧回填缺版本／赛前时刻证据。")

def devig(o):
    s = sum(1 / x for x in o)
    return [1 / x / s for x in o]

def brier(ps, actual):
    # actual: 0=H,1=D,2=A
    t = [1.0 if i == actual else 0.0 for i in range(3)]
    return sum((p - x) ** 2 for p, x in zip(ps, t))

def logloss(ps, actual):
    return -math.log(max(ps[actual], 1e-9))

def rps(ps, actual):
    # 有序：H>D>A
    t = [1.0 if i == actual else 0.0 for i in range(3)]
    s = 0
    for k in range(2):
        s += (sum(ps[:k+1]) - sum(t[:k+1])) ** 2
    return s / 2

ACT = {"H": 0, "D": 1, "A": 2}

# ---------- A. 校准：fused / model / market(FD closing) / elo ----------
def market_probs(r):
    c = r.get("fd_closing")
    if c and all(c): return devig(c)
    return None

def elo_probs(r):
    # 从 signals 里找 elo 信号？没有则跳过。用 elo_pre 粗算
    return None

variants = {"fused": [], "model": [], "market": []}
for r in recs:
    a = ACT[r["actual"]]
    variants["fused"].append((r["p"], a))
    s = r.get("signals") or {}
    if "model" in s: variants["model"].append((s["model"], a))
    mp = market_probs(r)
    if mp: variants["market"].append((mp, a))

print("\n=== A. 校准 ===")
for name, vs in variants.items():
    n = len(vs)
    br = sum(brier(p, a) for p, a in vs) / n
    ll = sum(logloss(p, a) for p, a in vs) / n
    rp = sum(rps(p, a) for p, a in vs) / n
    hit = sum(1 for p, a in vs if max(range(3), key=lambda i: p[i]) == a) / n
    # ECE (10 bins on max prob)
    bins = defaultdict(list)
    for p, a in vs:
        bins[min(int(max(p) * 10), 9)].append((max(p), int(max(range(3), key=lambda i: p[i]) == a)))
    ece = sum(abs(sum(hit for p, hit in v)/len(v) - sum(p for p, hit in v)/len(v)) * len(v)/n for v in bins.values())
    print(f"  {name:8s} n={n} Brier={br:.4f} LogLoss={ll:.4f} RPS={rp:.4f} 方向={hit:.1%} ECE={ece:.4f}")

# ---------- B. 背离：模型 vs 市场(FD closing) ----------
print("\n=== B. 严重背离复盘（模型方向 vs FD收盘方向） ===")
div = [r for r in recs if r.get("divergence")]  # 非空即严重背离
print(f"  严重背离 {len(div)}/{len(recs)} = {len(div)/len(recs):.1%}")
mw = sw = neither = 0
for r in div:
    a = ACT[r["actual"]]
    mp = market_probs(r)
    if not mp: neither += 1; continue
    md = max(range(3), key=lambda i: mp[i])
    sd = max(range(3), key=lambda i: (r.get("signals") or {}).get("model", r["p"])[i])
    if md == a and sd == a: neither += 1  # 同向不算背离（理论上不应发生）
    elif md == a: mw += 1
    elif sd == a: sw += 1
    else: neither += 1
print(f"  市场赢 {mw} | 原始模型信号赢 {sw} | 都没中 {neither}（不等于最终融合模型）")
dec = mw + sw
if dec: print(f"  decisive中: 市场 {mw/dec:.1%} / 模型 {sw/dec:.1%}")

# ---------- C. 初盘→收盘漂移 ----------
print("\n=== C. 初盘→收盘漂移（FD） ===")
for label, cond in [("热门被追捧", lambda d: d > 0.02), ("基本不动", lambda d: abs(d) <= 0.02), ("热门被抛弃", lambda d: d < -0.02)]:
    ss = []
    for r in recs:
        o, c = r.get("fd_opening"), r.get("fd_closing")
        if not (o and c and all(o) and all(c)): continue
        po, pc = devig(o), devig(c)
        fav = max(range(3), key=lambda i: pc[i])
        d = pc[fav] - po[fav]
        if cond(d):
            ss.append(1 if ACT[r["actual"]] == fav else 0)
    if ss: print(f"  {label}: n={len(ss)} 热门打出 {sum(ss)/len(ss):.1%}")

# ---------- D. 竞彩让球：让平诊断 ----------
print("\n=== D. 竞彩让球（rq）让平诊断 ===")
from engine.poisson import score_matrix
def margin_probs(lam_h, lam_a, rho=-0.13):
    m = score_matrix(lam_h, lam_a, rho=rho)
    mp = defaultdict(float)
    for i in range(len(m)):
        for j in range(len(m[0])):
            mp[i-j] += m[i][j]
    return mp

tot_rq = 0; hit_hp = 0; pred_hp_sum = 0; hp_n = 0
rq_hit = 0; rq_n = 0
for r in recs:
    rq = r.get("jc_rq", 0)
    if rq == 0: continue
    lam_h, lam_a = r["lam"]
    if not lam_h: continue
    try: mp = margin_probs(lam_h, lam_a)
    except Exception: continue
    hg, ag = map(int, r["score"].split("-"))
    margin = hg - ag
    # 竞彩：让胜 margin>-rq? 注意 rq=-1 表示主让1球
    # 让胜: margin + rq > 0 → margin > -rq
    actual_hp = "让平" if margin == -rq else ("让胜" if margin > -rq else "让负")
    p_hp = mp.get(-rq, 0)
    # 模型让球方向
    p_rs = sum(p for mgn, p in mp.items() if mgn > -rq)
    p_rp = p_hp
    p_rf = sum(p for mgn, p in mp.items() if mgn < -rq)
    pred = max([("让胜", p_rs), ("让平", p_rp), ("让负", p_rf)], key=lambda x: x[1])[0]
    tot_rq += 1
    pred_hp_sum += p_hp
    if actual_hp == "让平": hit_hp += 1
    if pred == actual_hp: rq_hit += 1
    rq_n += 1
if rq_n:
    print(f"  有让球 {rq_n} 场")
    print(f"  实际让平 {hit_hp}/{rq_n}={hit_hp/rq_n:.1%} | 模型平均预测让平概率 {pred_hp_sum/rq_n:.1%}")
    print(f"  模型让球方向命中 {rq_hit}/{rq_n}={rq_hit/rq_n:.1%}")

# ---------- E. 等级 / 总进球 / 半场 ----------
print("\n=== E. 其他 ===")
for g in ["A", "B", "C"]:
    ss = [r for r in recs if r.get("grade") == g]
    if ss:
        hit = sum(1 for r in ss if max(range(3), key=lambda i: r["p"][i]) == ACT[r["actual"]]) / len(ss)
        print(f"  {g}级 n={len(ss)} 方向 {hit:.1%}")
# 总进球
errs = []
for r in recs:
    eg = r.get("expected_goals")
    exp = eg if isinstance(eg, (int, float)) else (eg.get("expected_total") or eg.get("total") if isinstance(eg, dict) else None)
    hg, ag = map(int, r["score"].split("-"))
    if exp: errs.append(exp - (hg + ag))
if errs:
    print(f"  期望总进球-实际: 均值 {sum(errs)/len(errs):+.2f}, MAE {sum(abs(e) for e in errs)/len(errs):.2f}")
# 半场
ht_hit, ht_n = 0, 0
for r in recs:
    ht = r.get("half_time") or {}
    if "p_home" in ht:
        ht_n += 1
        pred = max(["H","D","A"], key=lambda k: ht[{"H":"p_home","D":"p_draw","A":"p_away"}[k]])
        if pred == r["htr"]: ht_hit += 1
print(f"  半场方向: {ht_hit}/{ht_n}={ht_hit/ht_n:.1%}" if ht_n else "  半场: 无数据")
# 第一比分
bs = sum(1 for r in recs if (r.get("top_score") or {}).get("score") == r["score"])
print(f"  第一比分命中: {bs}/{len(recs)}={bs/len(recs):.1%}")
