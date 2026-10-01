"""第三轮：主客进球相关性诊断。"""
import json, math
from collections import defaultdict, Counter
import sys
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.poisson import score_matrix

RHO=-0.13
recs=[json.loads(l) for l in open("/home/hatch/workspace/football-prediction-v2/hidden_backfill_jingcai.jsonl")]
rows=[]
for r in recs:
    lam=r.get("lam")
    if not lam or not lam[0]: continue
    hg,ag=map(int,r["score"].split("-"))
    rows.append((lam[0],lam[1],hg,ag))
n=len(rows)

# 经验相关系数
mh=sum(h for _,_,h,_ in rows)/n; ma=sum(a for _,_,_,a in rows)/n
cov=sum((h-mh)*(a-ma) for _,_,h,a in rows)/n
vh=sum((h-mh)**2 for _,_,h,_ in rows)/n; va=sum((a-ma)**2 for _,_,_,a in rows)/n
corr_e=cov/math.sqrt(vh*va)
print(f"[经验] corr(hg,ag)={corr_e:+.3f}  E[hg]={mh:.2f} E[ag]={ma:.2f}")

# 模型隐含相关系数(混合): E[h*a]-E[h]E[a] over mixture
Eh=Ea=Eha=Eh2=Ea2=0.0
for lh,la,h,a in rows:
    m=score_matrix(lh,la,rho=RHO,max_goals=10)
    eh=sum(i*m[i][j] for i in range(len(m)) for j in range(len(m[0])))
    ea=sum(j*m[i][j] for i in range(len(m)) for j in range(len(m[0])))
    eha=sum(i*j*m[i][j] for i in range(len(m)) for j in range(len(m[0])))
    eh2=sum(i*i*m[i][j] for i in range(len(m)) for j in range(len(m[0])))
    ea2=sum(j*j*m[i][j] for i in range(len(m)) for j in range(len(m[0])))
    Eh+=eh/n; Ea+=ea/n; Eha+=eha/n; Eh2+=eh2/n; Ea2+=ea2/n
cov_m=Eha-Eh*Ea; corr_m=cov_m/math.sqrt((Eh2-Eh**2)*(Ea2-Ea**2))
print(f"[模型] corr(hg,ag)={corr_m:+.3f} (rho={RHO})")
print(f"  经验协方差 {cov:+.3f} vs 模型 {cov_m:+.3f}")

# 条件分布: hg=2 时 ag 的分布
print("\n[条件分布 hg=2]")
sub=[(lh,la,a) for lh,la,h,a in rows if h==2]
ec=Counter(a for _,_,a in sub)
mp=defaultdict(float)
for lh,la,_ in sub:
    m=score_matrix(lh,la,rho=RHO,max_goals=10)
    tot2=sum(m[2][j] for j in range(len(m[0])))
    for j in range(len(m[0])): mp[j]+= (m[2][j]/tot2 if tot2>0 else 0)/len(sub)
for a in range(5):
    e=ec.get(a,0)/len(sub)
    print(f"  ag={a}: 实际 {e:.1%} 模型 {mp[a]:.1%} (n={len(sub)})")

print("\n[条件分布 hg=1]")
sub=[(lh,la,a) for lh,la,h,a in rows if h==1]
ec=Counter(a for _,_,a in sub); mp=defaultdict(float)
for lh,la,_ in sub:
    m=score_matrix(lh,la,rho=RHO,max_goals=10)
    tot1=sum(m[1][j] for j in range(len(m[0])))
    for j in range(len(m[0])): mp[j]+= (m[1][j]/tot1 if tot1>0 else 0)/len(sub)
for a in range(5):
    e=ec.get(a,0)/len(sub)
    print(f"  ag={a}: 实际 {e:.1%} 模型 {mp[a]:.1%} (n={len(sub)})")

print("\n[条件分布 hg=0]")
sub=[(lh,la,a) for lh,la,h,a in rows if h==0]
ec=Counter(a for _,_,a in sub); mp=defaultdict(float)
for lh,la,_ in sub:
    m=score_matrix(lh,la,rho=RHO,max_goals=10)
    tot0=sum(m[0][j] for j in range(len(m[0])))
    for j in range(len(m[0])): mp[j]+= (m[0][j]/tot0 if tot0>0 else 0)/len(sub)
for a in range(5):
    e=ec.get(a,0)/len(sub)
    print(f"  ag={a}: 实际 {e:.1%} 模型 {mp[a]:.1%} (n={len(sub)})")
