"""第二轮诊断：比分级对比 + 让球方向分解 + margin 偏差来源。"""
import json, math
from collections import defaultdict, Counter
import sys
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.poisson import score_matrix

RHO = -0.13
def smatrix(lh, la):
    return score_matrix(lh, la, rho=RHO, max_goals=10)

recs = [json.loads(l) for l in open("/home/hatch/workspace/football-prediction-v2/hidden_backfill_jingcai.jsonl")]
rows = []
for r in recs:
    lam = r.get("lam")
    if not lam or not lam[0]: continue
    hg, ag = map(int, r["score"].split("-"))
    rows.append((r, lam[0], lam[1], hg, ag))

# ---------- A. 常见比分：模型平均概率 vs 实际频率 ----------
scores = ["0-0","1-0","0-1","1-1","2-0","0-2","2-1","1-2","2-2","3-0","0-3","3-1","1-3"]
mod_p = defaultdict(float); emp_c = Counter()
for r, lh, la, hg, ag in rows:
    m = smatrix(lh, la)
    for i in range(len(m)):
        for j in range(len(m[0])):
            if f"{i}-{j}" in scores: mod_p[f"{i}-{j}"] += m[i][j]
    emp_c[f"{hg}-{ag}"] += 1
n = len(rows)
print("[常见比分] 实际频率 vs 模型平均概率")
for s in scores:
    e = emp_c.get(s,0)/n; mp = mod_p[s]/n
    flag = " <--" if abs(e-mp) > 0.015 else ""
    print(f"  {s:>4}: 实际 {e:6.1%} 模型 {mp:6.1%} 差 {(e-mp)*100:+5.1f}pp{flag}")

# ---------- B. 按 rq 符号分解让平 ----------
print("\n[让平×让球方向]")
for sgn, name in [(-1,"主让球 rq<0"), (1,"客让球 rq>0")]:
    sub = [(r,lh,la,hg,ag) for r,lh,la,hg,ag in rows if r.get("jc_rq",0)!=0 and (r["jc_rq"]<0)==(sgn<0)]
    P=[]; A=[]
    for r,lh,la,hg,ag in sub:
        m = smatrix(lh,la); mp=defaultdict(float)
        for i in range(len(m)):
            for j in range(len(m[0])): mp[i-j]+=m[i][j]
        P.append(mp.get(-r["jc_rq"],0.0)); A.append(1 if (hg-ag)==-r["jc_rq"] else 0)
    ph=sum(P)/len(P); ah=sum(A)/len(A); se=math.sqrt(ah*(1-ah)/len(A))
    print(f"  {name}: n={len(sub)} 预测 {ph:.1%} 实际 {ah:.1%} 差 {ah-ph:+.1%} (z={(ah-ph)/se:+.2f})")

# ---------- C. 每场 E[margin] 偏差 ----------
errs=[]
for r,lh,la,hg,ag in rows:
    m=smatrix(lh,la); mu=sum((i-j)*m[i][j] for i in range(len(m)) for j in range(len(m[0])))
    errs.append((hg-ag)-mu)
print(f"\n[margin偏差] 每场(实际-E[margin]) 均值 {sum(errs)/len(errs):+.3f} (n={len(errs)})")
# 按 E[margin] 符号分组
for cond,name in [(lambda e:e>0.3,"E[margin]>0.3 主强"),(lambda e:abs(e)<=0.3,"|E|<=0.3 均势"),(lambda e:e<-0.3,"E[margin]<-0.3 客强")]:
    sub=[e for (r,lh,la,hg,ag),e in zip(rows,errs)
         if cond(sum((i-j)*smatrix(lh,la)[i][j] for i in range(11) for j in range(11)))]
    if sub: print(f"  {name}: n={len(sub)} 平均偏差 {sum(sub)/len(sub):+.3f}")

# ---------- D. 诊断性平移：margin 分布整体平移 δ 时让平预测的变化 ----------
print("\n[诊断性平移] P(让平) 随整体 margin 平移 δ 的变化 (描述性, 非拟合)")
rq_rows=[(r,lh,la) for r,lh,la,hg,ag in rows if r.get("jc_rq",0)!=0]
for d in [0.0, 0.1, 0.13, 0.2, 0.3]:
    tot=0.0
    for r,lh,la in rq_rows:
        m=smatrix(lh,la); mp=defaultdict(float)
        for i in range(len(m)):
            for j in range(len(m[0])): mp[i-j]+=m[i][j]
        # 近似: 分布平移 δ ≈ P(margin=-rq) 用 P(margin=-rq-δ) 线性插值
        k=-r["jc_rq"]; lo=math.floor(k-d); frac=(k-d)-lo
        tot += mp.get(lo,0)*(1-frac)+mp.get(lo+1,0)*frac
    print(f"  δ={d:+.2f}: 平均预测让平 {tot/len(rq_rows):.1%}")

# ---------- E. 主胜1球 vs 客胜1球：比分级 ----------
print("\n[E. 1球小胜]")
for s in ["1-0","0-1","2-1","1-2"]:
    e=emp_c.get(s,0)/n; mp=mod_p[s]/n
    print(f"  {s}: 实际 {e:.1%} 模型 {mp:.1%} 差 {(e-mp)*100:+.1f}pp")
