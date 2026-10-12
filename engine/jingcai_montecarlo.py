"""竞彩最终比分矩阵的可重现抽样；不用于宣称预测更准确。"""
from bisect import bisect_right
import math
import random


def simulate_matrix(matrix, *, n, seed=0, completeness=100, min_score=0):
    source = "jingcai_final_score_matrix"
    if completeness < min_score:
        return {"ran": False, "probability_source": source,
                "reason": f"Monte Carlo：未运行（完整度 {completeness} < {min_score}）"}
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        raise ValueError("mc_n必须为正整数")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("竞彩mc_seed必须为整数")
    if not matrix or any(len(row) != len(matrix) for row in matrix):
        raise ValueError("比分矩阵必须为非空方阵")
    cells=[];cdf=[];mass=0.0;exact=[0.0,0.0,0.0]
    for h,row in enumerate(matrix):
        for a,p in enumerate(row):
            if isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or p < 0:
                raise ValueError("比分矩阵含非法概率")
            if p:
                mass+=p;cells.append((h,a));cdf.append(mass)
                exact[0 if h>a else 1 if h==a else 2]+=p
    if abs(mass-1)>1e-8:
        raise ValueError("比分矩阵概率和必须为1")
    cdf=[v/mass for v in cdf];cdf[-1]=1.0
    exact=[v/mass for v in exact]
    rng=random.Random(seed);outcomes=[0,0,0];scores={};goals={};over=0
    for _ in range(n):
        h,a=cells[bisect_right(cdf,rng.random())]
        outcomes[0 if h>a else 1 if h==a else 2]+=1
        scores[(h,a)]=scores.get((h,a),0)+1
        goals[h+a]=goals.get(h+a,0)+1;over+=h+a>=3
    probs=[v/n for v in outcomes]
    top=sorted(scores.items(),key=lambda kv:(-kv[1],kv[0]))[:5]
    return {"ran":True,"n":n,"seed":seed,"probability_source":source,
            "p_home":round(probs[0],4),"p_draw":round(probs[1],4),"p_away":round(probs[2],4),
            "p_full":probs,"over25":round(over/n,4),
            "top_scores":[{"score":f"{h}-{a}","prob":round(count/n,4)} for (h,a),count in top],
            "total_goals_full":{str(k):v/n for k,v in sorted(goals.items())},
            "reference_probabilities":exact,
            "wdl_standard_errors":[math.sqrt(p*(1-p)/n) for p in exact],
            "max_probability_sampling_error":max(abs(p-q) for p,q in zip(probs,exact)),
            "qualification":"simulation of the final model; not independent accuracy validation"}
