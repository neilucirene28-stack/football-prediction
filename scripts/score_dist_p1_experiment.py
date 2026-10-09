#!/usr/bin/env python3
"""GPT比分分布替换 P1：双Poisson混合 vs 独立负二项，并行实验。

流程（每场）：
  基线: Q0=Poisson(λ,ρ) → IPF(p) → (μH,μA), LL0
  实验A: Q=mixture(λ,δ,ρ) → constrain(Q, p, μH,μA)
  实验B: Q=negbin(λ,k,ρ)  → constrain(Q, p, μH,μA)
参数 δ,k,ρ 只在训练折上用比分对数似然拟合；硬约束用 P0 脚本口径验收。

输出 docs/score-dist-p1-report.md 所需的全部数字（本脚本打印 JSON）。
"""
import json
import math
import random
import sys
from collections import defaultdict

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.poisson import ipf_to_marginals, match_probs
from engine.score_constrain import constrain_matrix, matrix_means
from engine.score_dist import mixture_matrix, negbin_matrix, poisson_baseline_matrix

BACKFILL = "/home/hatch/workspace/football-prediction-v2/hidden_backfill_jingcai.jsonl"
EPS = 1e-12
RHO_GRID = [-0.20, -0.15, -0.13, -0.10, -0.05, 0.0]
DELTA_GRID = [0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]
K_GRID = [1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0, 40.0, 100.0]


def load():
    rows = []
    for line in open(BACKFILL):
        r = json.loads(line)
        h, a = (int(x) for x in r["score"].split("-"))
        rows.append({
            "date": r["date"],
            "lam_h": r["lam"][0], "lam_a": r["lam"][1],
            "p": tuple(r["p"]),
            "h": h, "a": a,
        })
    rows.sort(key=lambda x: x["date"])
    return rows


def baseline(row):
    """v2.10基线：Poisson+DC(ρ=-0.13) → IPF(p)。返回 (μH, μA, LL0, P0)。"""
    Q, _ = poisson_baseline_matrix(row["lam_h"], row["lam_a"], rho=-0.13)
    P = ipf_to_marginals(Q, row["p"])
    mh, ma = matrix_means(P)
    n = len(P)
    p = P[row["h"]][row["a"]] if row["h"] < n and row["a"] < n else EPS
    return mh, ma, -math.log(max(p, EPS)), P


def constrained_ll(row, mu_h, mu_a, builder, **kw):
    """候选分布 → constrain → 实际比分的对数似然。返回 (ll, info)。"""
    Q, _ = builder(row["lam_h"], row["lam_a"], rho=kw.get("rho", -0.13),
                   **{k: v for k, v in kw.items() if k != "rho"})
    P, info = constrain_matrix(Q, row["p"], mu_h, mu_a)
    n = len(P)
    p = P[row["h"]][row["a"]] if row["h"] < n and row["a"] < n else EPS
    return -math.log(max(p, EPS)), info


def evaluate(rows, base, bkey, builder, params, pname):
    """在测试集上评估。返回每场 (ll, info, tail_probs)。"""
    pv, rho = params
    out = []
    for row in rows:
        mu_h, mu_a = base[bkey(row)]["mu"]
        Q, _ = builder(row["lam_h"], row["lam_a"], **{pname: pv, "rho": rho})
        P, info = constrain_matrix(Q, row["p"], mu_h, mu_a)
        n = len(P)
        p = P[row["h"]][row["a"]] if row["h"] < n and row["a"] < n else EPS
        ll = -math.log(max(p, EPS))
        t5 = sum(P[i][j] for i in range(n) for j in range(n) if i + j >= 5)
        s4 = sum(P[i][j] for i in range(n) for j in range(n) if i >= 4 or j >= 4)
        z0 = P[0][0] if n > 0 else 0.0
        out.append({"ll": ll, "fallback": info.get("fallback", False),
                    "t5": t5, "s4": s4, "z0": z0,
                    "e12": info.get("max_1x2_err"), "em": info.get("max_mean_err"),
                    "actual_total": row["h"] + row["a"],
                    "actual_max": max(row["h"], row["a"]),
                    "actual_z0": 1 if (row["h"], row["a"]) == (0, 0) else 0})
    return out


def main():
    rows = load()
    # 基线（μH,μA,LL0）逐场预计算
    base = {}
    for idx, row in enumerate(rows):
        mh, ma, ll0, _ = baseline(row)
        base[row["date"] + str(idx)] = {"mu": (mh, ma), "ll0": ll0}

    dates = sorted(set(r["date"] for r in rows))
    print(json.dumps({"n": len(rows), "dates": [dates[0], dates[-1]],
                      "n_dates": len(dates)}))

    # ---- walk-forward：expanding window，按日期 ----
    # 训练集 = 日期 < d 的全部；测试日 d 从第13个日期开始
    wf = {"A": [], "B": [], "base": []}
    params_log = []
    for di in range(12, len(dates)):
        d = dates[di]
        train = [r for r in rows if r["date"] < d]
        test = [r for r in rows if r["date"] == d]
        # 用全局索引做base key
        idx_of = {id(r): i for i, r in enumerate(rows)}
        bkey = lambda r: r["date"] + str(idx_of[id(r)])
        # 拟合
        (dlt, rho_a), _, _ = fit_param_wf(train, base, bkey, mixture_matrix, DELTA_GRID, "delta")
        (kk, rho_b), _, _ = fit_param_wf(train, base, bkey, negbin_matrix, K_GRID, "k")
        params_log.append({"date": d, "A": [dlt, rho_a], "B": [kk, rho_b],
                           "n_train": len(train), "n_test": len(test)})
        for r in test:
            mu_h, mu_a = base[bkey(r)]["mu"]
            ll0 = base[bkey(r)]["ll0"]
            lla, ia = constrained_ll(r, mu_h, mu_a, mixture_matrix, delta=dlt, rho=rho_a)
            llb, ib = constrained_ll(r, mu_h, mu_a, negbin_matrix, k=kk, rho=rho_b)
            wf["base"].append(ll0)
            wf["A"].append((lla, ia))
            wf["B"].append((llb, ib))

    def summ(xs):
        lls = [x[0] if isinstance(x, tuple) else x for x in xs]
        fb = sum(1 for x in xs if isinstance(x, tuple) and x[1].get("fallback"))
        return {"n": len(lls), "mean_ll": sum(lls) / len(lls),
                "fallback": fb}

    print("WALK_FORWARD")
    print(json.dumps({
        "base": summ(wf["base"]),
        "A_mixture": summ(wf["A"]),
        "B_negbin": summ(wf["B"]),
        "delta_A_vs_base": sum(a[0] - b for a, b in zip(wf["A"], wf["base"])) / len(wf["base"]),
        "delta_B_vs_base": sum(b_[0] - b for b_, b in zip(wf["B"], wf["base"])) / len(wf["base"]),
    }, indent=1))
    print("PARAMS_LOG")
    print(json.dumps(params_log))

    # ---- 固定留出集：训练 09-01~09-14，测试 09-15~09-20 ----
    train = [r for r in rows if r["date"] <= "2026-09-14"]
    test = [r for r in rows if r["date"] > "2026-09-14"]
    idx_of = {id(r): i for i, r in enumerate(rows)}
    bkey = lambda r: r["date"] + str(idx_of[id(r)])
    (dlt, rho_a), tra, _ = fit_param_wf(train, base, bkey, mixture_matrix, DELTA_GRID, "delta")
    (kk, rho_b), trb, _ = fit_param_wf(train, base, bkey, negbin_matrix, K_GRID, "k")
    evA = evaluate(test, base, bkey, mixture_matrix, (dlt, rho_a), "delta")
    evB = evaluate(test, base, bkey, negbin_matrix, (kk, rho_b), "k")
    ll0t = [base[bkey(r)]["ll0"] for r in test]
    print("HOLDOUT")
    print(json.dumps({
        "n_train": len(train), "n_test": len(test),
        "A_params": {"delta": dlt, "rho": rho_a}, "A_train_ll": tra,
        "B_params": {"k": kk, "rho": rho_b}, "B_train_ll": trb,
        "base_mean_ll": sum(ll0t) / len(ll0t),
        "A_mean_ll": sum(e["ll"] for e in evA) / len(evA),
        "B_mean_ll": sum(e["ll"] for e in evB) / len(evB),
        "A_fallback": sum(e["fallback"] for e in evA),
        "B_fallback": sum(e["fallback"] for e in evB),
        "A_max_e12": max(e["e12"] for e in evA),
        "A_max_em": max(e["em"] for e in evA),
        "B_max_e12": max(e["e12"] for e in evB),
        "B_max_em": max(e["em"] for e in evB),
        # 尾部：预测均值 vs 实际频率
        "actual_t5_rate": sum(1 for e in evA if e["actual_total"] >= 5) / len(evA),
        "A_pred_t5": sum(e["t5"] for e in evA) / len(evA),
        "B_pred_t5": sum(e["t5"] for e in evB) / len(evB),
        "actual_s4_rate": sum(1 for e in evA if e["actual_max"] >= 4) / len(evA),
        "A_pred_s4": sum(e["s4"] for e in evA) / len(evA),
        "B_pred_s4": sum(e["s4"] for e in evB) / len(evB),
        "actual_z0_rate": sum(e["actual_z0"] for e in evA) / len(evA),
        "A_pred_z0": sum(e["z0"] for e in evA) / len(evA),
        "B_pred_z0": sum(e["z0"] for e in evB) / len(evB),
    }, indent=1))

    # ---- bootstrap：按日期聚类，ΔLL 区间 ----
    rng = random.Random(20261009)
    by_date = defaultdict(list)
    for i, r in enumerate(test):
        by_date[r["date"]].append(i)
    dlist = list(by_date.keys())
    n_boot = 2000
    diffsA, diffsB = [], []
    for _ in range(n_boot):
        samp = [rng.choice(dlist) for _ in dlist]
        idxs = [i for d in samp for i in by_date[d]]
        dA = sum(evA[i]["ll"] - ll0t[i] for i in idxs) / len(idxs)
        dB = sum(evB[i]["ll"] - ll0t[i] for i in idxs) / len(idxs)
        diffsA.append(dA)
        diffsB.append(dB)
    diffsA.sort()
    diffsB.sort()
    print("BOOTSTRAP")
    print(json.dumps({
        "A_delta_ll_95ci": [diffsA[int(0.025 * n_boot)], diffsA[int(0.975 * n_boot)]],
        "B_delta_ll_95ci": [diffsB[int(0.025 * n_boot)], diffsB[int(0.975 * n_boot)]],
        "A_prob_better": sum(1 for x in diffsA if x < 0) / n_boot,
        "B_prob_better": sum(1 for x in diffsB if x < 0) / n_boot,
    }, indent=1))


def fit_param_wf(rows, base, bkey, builder, pgrid, pname):
    best, best_ll, detail = None, float("inf"), {}
    for pv in pgrid:
        for rho in RHO_GRID:
            tot, fb = 0.0, 0
            for row in rows:
                mu_h, mu_a = base[bkey(row)]["mu"]
                ll, info = constrained_ll(row, mu_h, mu_a, builder,
                                          **{pname: pv, "rho": rho})
                tot += ll
                fb += 1 if info.get("fallback") else 0
            detail[(pv, rho)] = (tot, fb)
            if tot < best_ll:
                best_ll, best = tot, (pv, rho)
    return best, best_ll, detail


if __name__ == "__main__":
    main()
