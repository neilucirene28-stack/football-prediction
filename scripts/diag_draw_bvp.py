"""第四轮(描述性, 非生产拟合): bivariate Poisson 正对角依赖对让平预测的影响示意。"""
import json, math
from collections import defaultdict
from math import exp, factorial

def poi(k, lam):
    return exp(-lam)*lam**k/factorial(k) if lam>0 else (1.0 if k==0 else 0.0)

def bvp_matrix(lh, la, c, max_goals=10):
    """Karlis-Ntzoufras bivariate Poisson: hg=X1+X3, ag=X2+X3, lam3=c*min(lh,la)."""
    lam3 = c*min(lh, la)
    l1, l2 = lh-lam3, la-lam3
    m=[[0.0]*(max_goals+1) for _ in range(max_goals+1)]
    for i in range(max_goals+1):
        for j in range(max_goals+1):
            s=0.0
            for k in range(0, min(i,j)+1):
                s+=poi(i-k,l1)*poi(j-k,l2)*poi(k,lam3)
            m[i][j]=s
    return m

recs=[json.loads(l) for l in open("/home/hatch/workspace/football-prediction-v2/hidden_backfill_jingcai.jsonl")]
rows=[]
for r in recs:
    lam=r.get("lam")
    if not lam or not lam[0]: continue
    hg,ag=map(int,r["score"].split("-"))
    rows.append((r,lam[0],lam[1],hg,ag))
n=len(rows)
rq_rows=[x for x in rows if x[0].get("jc_rq",0)!=0]

print("[描述性] 不同对角依赖强度 c 下的平均预测让平 (实际 22.7%)")
print(" c = lam3/min(lh,la); c=0 即独立 Poisson")
for c in [0.0, 0.1, 0.2, 0.3, 0.4]:
    tot=0.0
    for r,lh,la,hg,ag in rq_rows:
        m=bvp_matrix(lh,la,c); mp=defaultdict(float)
        for i in range(len(m)):
            for j in range(len(m[0])): mp[i-j]+=m[i][j]
        tot+=mp.get(-r["jc_rq"],0.0)
    print(f"  c={c:.1f}: {tot/len(rq_rows):.1%}")

# 描述性对数似然比较 (全样本, 仅示意方向)
print("\n[描述性] 全样本比分对数似然 (越高越好; 仅示意, 非验证)")
import sys
sys.path.insert(0,"/home/hatch/workspace/football-prediction-v2")
from engine.poisson import score_matrix
for name, fn in [("独立Poisson", lambda lh,la: score_matrix(lh,la,rho=0.0,max_goals=10)),
                 ("DC rho=-0.13(生产)", lambda lh,la: score_matrix(lh,la,rho=-0.13,max_goals=10)),
                 ("BVP c=0.2", lambda lh,la: bvp_matrix(lh,la,0.2)),
                 ("BVP c=0.3", lambda lh,la: bvp_matrix(lh,la,0.3))]:
    ll=0.0
    for r,lh,la,hg,ag in rows:
        m=fn(lh,la); p=m[hg][ag] if hg<=10 and ag<=10 else 1e-9
        ll+=math.log(max(p,1e-9))
    print(f"  {name}: {ll:.1f}")

# BVP c=0.2 下的关键比分
print("\n[描述性] BVP c=0.2 的关键比分 实际 vs 模型")
mod_p=defaultdict(float)
for r,lh,la,hg,ag in rows:
    m=bvp_matrix(lh,la,0.2)
    for s in ["0-0","1-0","0-1","1-1","2-0","0-2","2-1","1-2","2-2"]:
        i,j=map(int,s.split("-")); mod_p[s]+=m[i][j]
from collections import Counter
ec=Counter(f"{hg}-{ag}" for _,_,_,hg,ag in rows)
for s in ["0-0","1-0","0-1","1-1","2-0","0-2","2-1","1-2","2-2"]:
    e=ec.get(s,0)/n; mp=mod_p[s]/n
    print(f"  {s}: 实际 {e:.1%} 模型 {mp:.1%} 差 {(e-mp)*100:+.1f}pp")
