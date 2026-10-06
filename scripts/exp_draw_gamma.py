#!/usr/bin/env python3
"""让平校准离线实验：方案 A（margin 温度校准 P̃(k) ∝ P(k)^γ）。

诚实设计：
- Phase 1（194场欧洲联赛）拟合 γ（最大化让球三分类样本内对数似然）
- Phase 2（224场 J2/韩K/美职/国家队等）做纯样本外验证
- 跨联赛、跨赛事类型，是比 expanding window 更严格的泛化检验
- 不碰生产参数，只出实验报告
"""
import json, math, sys
from collections import defaultdict

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.poisson import score_matrix

BASE = "/home/hatch/workspace/football-prediction-v2"
RHO = -0.13


def load(path):
    recs = [json.loads(l) for l in open(path)]
    return [r for r in recs if r["actual"] in ("H", "D", "A")
            and r["jc_rq"] not in (None, 0) and r.get("lam")]


def margin_pmf(lam_h, lam_a):
    m = score_matrix(lam_h, lam_a, rho=RHO)
    n = len(m)
    pmf = defaultdict(float)
    for i in range(n):
        for j in range(n):
            pmf[i - j] += m[i][j]
    return pmf


def hdc_from_pmf(pmf, rq):
    ph = sum(p for k, p in pmf.items() if k > -rq)
    pd = pmf.get(-rq, 0.0)
    pa = 1.0 - ph - pd
    return (ph, pd, pa)


def temper(pmf, gamma):
    t = {k: p ** gamma for k, p in pmf.items() if p > 0}
    s = sum(t.values())
    return {k: v / s for k, v in t.items()}


def actual_hdc(r):
    hg, ag = (int(x) for x in r["score"].split("-"))
    am = (hg - ag) + r["jc_rq"]
    return 0 if am > 0 else (1 if am == 0 else 2)


def loglik(recs, gamma):
    ll = 0.0
    for r in recs:
        pmf = margin_pmf(*r["lam"])
        if gamma != 1.0:
            pmf = temper(pmf, gamma)
        p = hdc_from_pmf(pmf, r["jc_rq"])
        ll += math.log(max(p[actual_hdc(r)], 1e-12))
    return ll


def brier_draw(recs, gamma):
    """让平单项 Brier。"""
    s = 0.0
    for r in recs:
        pmf = margin_pmf(*r["lam"])
        if gamma != 1.0:
            pmf = temper(pmf, gamma)
        p = hdc_from_pmf(pmf, r["jc_rq"])
        y = 1.0 if actual_hdc(r) == 1 else 0.0
        s += (p[1] - y) ** 2
    return s / len(recs)


def brier_3class(recs, gamma):
    s = 0.0
    for r in recs:
        pmf = margin_pmf(*r["lam"])
        if gamma != 1.0:
            pmf = temper(pmf, gamma)
        p = hdc_from_pmf(pmf, r["jc_rq"])
        a = actual_hdc(r)
        s += sum((p[i] - (1.0 if i == a else 0.0)) ** 2 for i in range(3))
    return s / len(recs)


def main():
    p1 = load(f"{BASE}/hidden_backfill_jingcai.jsonl")
    p2 = load(f"{BASE}/hidden_backfill_phase2.jsonl")
    print(f"Phase1 n={len(p1)}（拟合），Phase2 n={len(p2)}（纯样本外）")

    # --- 拟合 γ（Phase 1 网格搜索） ---
    best_g, best_ll = 1.0, loglik(p1, 1.0)
    grid = [round(0.80 + 0.02 * i, 2) for i in range(36)]  # 0.80..1.50
    for g in grid:
        ll = loglik(p1, g)
        if ll > best_ll:
            best_ll, best_g = ll, g
    print(f"\nPhase1 拟合: 最优 γ={best_g}，loglik {loglik(p1,1.0):.1f} → {best_ll:.1f} "
          f"(Δ={best_ll-loglik(p1,1.0):+.1f})")

    # --- 样本外验证（Phase 2） ---
    print("\n==== Phase 2 样本外 ====")
    for name, g in (("基线 γ=1.0", 1.0), (f"校准 γ={best_g}", best_g)):
        bd = brier_draw(p2, g)
        b3 = brier_3class(p2, g)
        ll = loglik(p2, g)
        print(f"{name}: 让平Brier={bd:.4f} 三分类Brier={b3:.4f} loglik={ll:.1f}")

    # 配对 t 检验（让平 Brier 差）
    diffs = []
    for r in p2:
        pmf = margin_pmf(*r["lam"])
        p0 = hdc_from_pmf(pmf, r["jc_rq"])
        p1_ = hdc_from_pmf(temper(pmf, best_g), r["jc_rq"])
        y = 1.0 if actual_hdc(r) == 1 else 0.0
        diffs.append((p0[1] - y) ** 2 - (p1_[1] - y) ** 2)  # 正=校准更好
    n = len(diffs)
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    t = mean / math.sqrt(var / n)
    print(f"\n配对检验（让平Brier 基线-校准）: 均值差={mean:.5f}, t={t:.2f}, n={n}")
    print("  （t>1.96 为 5% 显著改进）")

    # 让平分位校准
    print("\n让平概率分位校准（Phase 2 样本外）：")
    rows = []
    for r in p2:
        pmf = margin_pmf(*r["lam"])
        p = hdc_from_pmf(temper(pmf, best_g), r["jc_rq"])
        rows.append((p[1], 1.0 if actual_hdc(r) == 1 else 0.0))
    rows.sort()
    q = len(rows) // 5
    for i in range(5):
        seg = rows[i * q:(i + 1) * q] if i < 4 else rows[i * q:]
        mp = sum(x[0] for x in seg) / len(seg)
        ma = sum(x[1] for x in seg) / len(seg)
        print(f"  五分位{i+1}: n={len(seg)} 预测均值 {mp:.1%} 实际 {ma:.1%}")


if __name__ == "__main__":
    main()
