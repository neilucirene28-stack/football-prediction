"""让平低估分布诊断：重建模型净胜球分布 vs 实际净胜球分布。"""
import json, math
from collections import defaultdict
import sys
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.poisson import score_matrix

RHO_PROD = -0.13  # 生产 predictor 配置

def margin_dist(lam_h, lam_a, rho=RHO_PROD, max_goals=10):
    m = score_matrix(lam_h, lam_a, rho=rho, max_goals=max_goals)
    mp = defaultdict(float)
    for i in range(len(m)):
        for j in range(len(m[0])):
            mp[i - j] += m[i][j]
    return mp

recs = [json.loads(l) for l in open("/home/hatch/workspace/football-prediction-v2/hidden_backfill_jingcai.jsonl")]
print(f"总记录 {len(recs)}")

rows = []
for r in recs:
    lam = r.get("lam")
    if not lam or not lam[0]: continue
    hg, ag = map(int, r["score"].split("-"))
    rows.append({"r": r, "lh": lam[0], "la": lam[1], "margin": hg - ag,
                 "total": hg + ag, "rq": r.get("jc_rq", 0),
                 "league": r.get("jc_league"), "date": r.get("date")})
print(f"有 lam 记录 {len(rows)}")

# ---------- 1. 让平：预测 vs 实际 ----------
rq_rows = [x for x in rows if x["rq"] != 0]
n = len(rq_rows)
pred_hp = []; act_hp = []
for x in rq_rows:
    mp = margin_dist(x["lh"], x["la"])
    pred_hp.append(mp.get(-x["rq"], 0.0))
    act_hp.append(1 if x["margin"] == -x["rq"] else 0)
ph = sum(pred_hp)/n; ah = sum(act_hp)/n
se = math.sqrt(ah*(1-ah)/n)
print(f"\n[让平] n={n} 模型平均预测 {ph:.1%} | 实际 {ah:.1%} | 差 {ah-ph:+.1%} (SE={se:.1%}, z={(ah-ph)/se:+.2f})")

for arq in sorted(set(abs(x["rq"]) for x in rq_rows)):
    sub = [x for x in rq_rows if abs(x["rq"]) == arq]
    p = sum(margin_dist(x["lh"],x["la"]).get(-x["rq"],0) for x in sub)/len(sub)
    a = sum(1 for x in sub if x["margin"]==-x["rq"])/len(sub)
    print(f"  |rq|={arq}: n={len(sub)} 预测 {p:.1%} 实际 {a:.1%} 差 {a-p:+.1%}")

# ---------- 2. 净胜球分布：模型平均隐含 vs 实际 ----------
emp = defaultdict(int); mod = defaultdict(float)
for x in rq_rows:
    mp = margin_dist(x["lh"], x["la"])
    emp[x["margin"]] += 1
    for k, p in mp.items(): mod[k] += p
print("\n[净胜球分布] (让球场次)")
print(f"{'margin':>6} {'实际':>8} {'模型':>8} {'差(pp)':>8}")
tot = len(rq_rows)
for k in list(range(-4,5)):
    e = emp.get(k,0)/tot; m_ = mod.get(k,0)/tot
    print(f"{k:>6} {e:>8.1%} {m_:>8.1%} {(e-m_)*100:>+7.1f}")
for tail, name in [("tail_neg","<-4"), ("tail_pos",">4")]:
    ks = [k for k in emp if (k<-4 if tail=="tail_neg" else k>4)]
    e = sum(emp[k] for k in ks)/tot
    mk = [k for k in mod if (k<-4 if tail=="tail_neg" else k>4)]
    m_ = sum(mod[k] for k in mk)/tot
    print(f"{name:>6} {e:>8.1%} {m_:>8.1%} {(e-m_)*100:>+7.1f}")

# ---------- 3. 方差/均值 ----------
margins = [x["margin"] for x in rq_rows]
mean_e = sum(margins)/len(margins)
var_e = sum((m-mean_e)**2 for m in margins)/len(margins)
# 模型混合方差 = E[Var] + Var(E)
e_vars=[]; e_means=[]
for x in rq_rows:
    mp = margin_dist(x["lh"], x["la"])
    mu = sum(k*p for k,p in mp.items()); v = sum((k-mu)**2*p for k,p in mp.items())
    e_means.append(mu); e_vars.append(v)
mean_m = sum(e_means)/len(e_means)
var_m = sum(e_vars)/len(e_vars) + sum((mu-mean_m)**2 for mu in e_means)/len(e_means)
print(f"\n[矩] 实际 margin 均值 {mean_e:+.2f} 方差 {var_e:.2f} | 模型混合 均值 {mean_m:+.2f} 方差 {var_m:.2f} | 方差比 {var_e/var_m:.2f}")

# ---------- 4. 按总进球期望分组 ----------
print("\n[按总λ分组] 让平 预测vs实际")
bins = [(0,2.0),(2.0,2.5),(2.5,3.0),(3.0,99)]
for lo,hi in bins:
    sub=[x for x in rq_rows if lo <= x["lh"]+x["la"] < hi]
    if len(sub)<8: continue
    p=sum(margin_dist(x["lh"],x["la"]).get(-x["rq"],0) for x in sub)/len(sub)
    a=sum(1 for x in sub if x["margin"]==-x["rq"])/len(sub)
    print(f"  总λ∈[{lo},{hi}): n={len(sub)} 预测 {p:.1%} 实际 {a:.1%} 差 {a-p:+.1%}")

# ---------- 5. 按联赛 ----------
print("\n[按联赛] 让平 预测vs实际 (n>=10)")
lg=defaultdict(list)
for x in rq_rows: lg[x["league"]].append(x)
for l,sub in sorted(lg.items(), key=lambda kv:-len(kv[1])):
    if len(sub)<10: continue
    p=sum(margin_dist(x["lh"],x["la"]).get(-x["rq"],0) for x in sub)/len(sub)
    a=sum(1 for x in sub if x["margin"]==-x["rq"])/len(sub)
    print(f"  {l}: n={len(sub)} 预测 {p:.1%} 实际 {a:.1%} 差 {a-p:+.1%}")

# ---------- 6. rho 敏感性：换 rho 看让平预测 ----------
print("\n[rho敏感性] 全部让球场次平均预测让平")
for rho in [0.0, -0.05, -0.1, -0.13, -0.2, -0.3]:
    p=sum(margin_dist(x["lh"],x["la"],rho=rho).get(-x["rq"],0) for x in rq_rows)/len(rq_rows)
    print(f"  rho={rho:+.2f}: {p:.1%}")

# ---------- 7. 总进球偏差 ----------
errs=[(x["lh"]+x["la"])-x["total"] for x in rows]
print(f"\n[总进球] 预测-实际 均值 {sum(errs)/len(errs):+.2f} (n={len(rows)})")

# ---------- 8. 低λ比赛的离散：实际0-0/1-0/0-1/1-1占比 ----------
print("\n[低λ离散检查] 总λ<2.2 的比赛")
sub=[x for x in rows if x["lh"]+x["la"]<2.2]
from collections import Counter
c=Counter((min(x["margin"],9), x["total"]) for x in sub)
sc=Counter(f"{x['r']['score']}" for x in sub)
print(f"  n={len(sub)} 实际比分Top: {sc.most_common(6)}")
mp_avg=defaultdict(float)
for x in sub:
    mp=margin_dist(x["lh"],x["la"])
    for k,p in mp.items(): mp_avg[k]+=p/len(sub)
print(f"  模型平均 margin 分布: "+" ".join(f"{k}:{mp_avg[k]:.1%}" for k in range(-2,3)))
