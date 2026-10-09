"""P0验证：constrain_matrix 用 Poisson 输入复现 IPF 基准。"""
import sys
sys.path.insert(0, ".")

from engine.poisson import score_matrix, ipf_to_marginals, match_probs
from engine.score_constrain import (
    constrain_matrix, matrix_means, adaptive_max_goals, TOL_1X2, TOL_MEAN)
from engine.poisson import pmf

CASES = [
    # (lam_h, lam_a, rho, p_final)
    (1.80, 1.20, -0.13, (0.55, 0.26, 0.19)),
    (1.30, 0.20, -0.13, (0.651552, 0.20, 0.148448)),  # GPT验收case
    (2.50, 1.80, -0.10, (0.60, 0.22, 0.18)),
    (0.90, 1.60, -0.13, (0.25, 0.27, 0.48)),
    (3.20, 0.80, -0.05, (0.78, 0.14, 0.08)),
]


def main():
    print("=== P0验证：constrain_matrix 复现 IPF 基准 ===")
    worst_12 = 0.0
    worst_m = 0.0
    n_fallback = 0
    for idx, (lh, la, rho, pf) in enumerate(CASES):
        Q = score_matrix(lh, la, rho=rho)
        base = ipf_to_marginals(Q, pf)
        mh0, ma0 = matrix_means(base)
        P, info = constrain_matrix(Q, pf, mh0, ma0)
        # 与基准矩阵逐格对比
        n = len(Q)
        maxdiff = max(abs(P[i][j] - base[i][j])
                      for i in range(n) for j in range(n))
        worst_12 = max(worst_12, info["max_1x2_err"])
        worst_m = max(worst_m, info["max_mean_err"])
        if info["fallback"]:
            n_fallback += 1
        status = "FALLBACK" if info["fallback"] else "OK"
        print(f"case{idx}: u={info['u']:.6f} v={info['v']:.6f} "
              f"max格差={maxdiff:.2e} 1x2err={info['max_1x2_err']:.2e} "
              f"meanerr={info['max_mean_err']:.2e} [{status}]")

    print(f"\n最差1X2误差: {worst_12:.2e} (容差 {TOL_1X2:.0e})")
    print(f"最差均值误差: {worst_m:.2e} (容差 {TOL_MEAN:.0e})")
    print(f"回退次数: {n_fallback}/{len(CASES)}")

    # 自适应网格验证
    print("\n=== 自适应网格验证 ===")
    for lam in (1.5, 3.0, 5.0):
        m, err = adaptive_max_goals(lambda k: pmf(k, lam),
                                    lambda k: pmf(k, lam))
        print(f"λ={lam}: max_goals={m}, 截断误差={err:.2e}")

    ok = (worst_12 < TOL_1X2 and worst_m < TOL_MEAN and n_fallback == 0)
    print("\n" + ("P0验证通过 ✓" if ok else "P0验证失败 ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
