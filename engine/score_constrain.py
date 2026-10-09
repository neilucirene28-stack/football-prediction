"""比分矩阵约束层（GPT比分分布替换 P0）。

目标：在给定任意原始比分矩阵 Q 的情况下，构造最终矩阵 P，使得：
  1. 1X2 边际精确等于目标概率 π = (π_H, π_D, π_A)
  2. 主客进球期望精确等于目标均值 (μ_H, μ_A)

方法：区域内指数倾斜（exponential tilting）。
  P_ij = π_{c(i,j)} · Q_ij·exp(u·i + v·j) / Σ_{(a,b)∈c(i,j)} Q_ab·exp(u·a + v·b)
其中 c(i,j) ∈ {H,D,A} 为 (i,j) 所属的胜平负区域。
用自研 Newton 法解 (u,v) 使全局均值约束成立（不依赖 scipy）；
分母用 log-sum-exp 保证数值稳定。

失败时（不收敛/非正概率/截断过大）回退到输入矩阵的 IPF 结果，
保证调用方总能拿到合法矩阵。

用途：
  - P0：用 Poisson 输入验证约束层能精确复现现有 IPF 基准
  - P1：换上候选分布（Poisson混合/负二项/Gamma节奏）后，
       仍锁定 1X2 与进球均值，隔离检验分布形状的效果
"""
import math

from .poisson import ipf_to_marginals, match_probs

# 硬约束容差（GPT设计）
TOL_1X2 = 1e-8
TOL_MEAN = 1e-6
TOL_TAIL = 1e-6  # 自适应网格双边遗漏质量


def _region(i: int, j: int) -> int:
    """0=主胜(H), 1=平局(D), 2=客胜(A)。"""
    return 0 if i > j else (1 if i == j else 2)


def matrix_means(matrix) -> tuple[float, float]:
    """返回 (E[主队进球], E[客队进球])。"""
    n = len(matrix)
    mh = sum(i * matrix[i][j] for i in range(n) for j in range(n))
    ma = sum(j * matrix[i][j] for i in range(n) for j in range(n))
    return mh, ma


def _logsumexp(xs):
    """数值稳定的 log(sum(exp(x)))。"""
    if not xs:
        return float("-inf")
    m = max(xs)
    if m == float("-inf"):
        return m
    return m + math.log(sum(math.exp(x - m) for x in xs))


def _newton_solve(residual_fn, x0, tol=1e-12, max_iter=100):
    """2D Newton 法解 residual_fn(x)=0，数值雅可比。

    返回 (x, converged)。residual_fn 接受 [u,v] 返回 [r1, r2]。
    """
    x = [float(x0[0]), float(x0[1])]
    h = 1e-7
    for _ in range(max_iter):
        r = residual_fn(x)
        if abs(r[0]) < tol and abs(r[1]) < tol:
            return x, True
        # 数值雅可比
        r_uh = residual_fn([x[0] + h, x[1]])
        r_vh = residual_fn([x[0], x[1] + h])
        j11 = (r_uh[0] - r[0]) / h
        j12 = (r_vh[0] - r[0]) / h
        j21 = (r_uh[1] - r[1]) / h
        j22 = (r_vh[1] - r[1]) / h
        det = j11 * j22 - j12 * j21
        if abs(det) < 1e-15:
            return x, False
        # 解 J·dx = -r
        dx0 = (-r[0] * j22 + r[1] * j12) / det
        dx1 = (-j11 * r[1] + j21 * r[0]) / det
        # 阻尼防发散
        step = 1.0
        for _ in range(10):
            xn = [x[0] + step * dx0, x[1] + step * dx1]
            rn = residual_fn(xn)
            if abs(rn[0]) + abs(rn[1]) < abs(r[0]) + abs(r[1]):
                break
            step *= 0.5
        x = [x[0] + step * dx0, x[1] + step * dx1]
        if abs(step * dx0) < 1e-13 and abs(step * dx1) < 1e-13:
            r = residual_fn(x)
            return x, (abs(r[0]) < tol and abs(r[1]) < tol)
    r = residual_fn(x)
    return x, (abs(r[0]) < tol and abs(r[1]) < tol)


def _tilted_region_probs(Q, u: float, v: float):
    """对每个区域计算倾斜后的区域内分布（未乘 π_c）。

    返回 (regions, logZ)：regions[c] 为 {(i,j): 归一化权重}，
    logZ[c] 为该区域分母的对数。
    """
    n = len(Q)
    # 收集每个区域的 (i,j, log w_ij)
    members = ([], [], [])
    for i in range(n):
        for j in range(n):
            q = Q[i][j]
            if q <= 0:
                continue
            c = _region(i, j)
            members[c].append((i, j, math.log(q) + u * i + v * j))
    regions = []
    logZ = []
    for c in range(3):
        mem = members[c]
        if not mem:
            regions.append({})
            logZ.append(float("-inf"))
            continue
        logs = [lw for _, _, lw in mem]
        lz = _logsumexp(logs)
        probs = {(i, j): math.exp(lw - lz) for i, j, lw in mem}
        regions.append(probs)
        logZ.append(lz)
    return regions, logZ


def _mean_residual(uv, Q, pi, mu_h, mu_a):
    """给定 (u,v)，返回均值残差 [E_H - μ_H, E_A - μ_A]。"""
    u, v = float(uv[0]), float(uv[1])
    regions, _ = _tilted_region_probs(Q, u, v)
    eh = sum(pi[c] * sum(i * p for (i, j), p in regions[c].items())
             for c in range(3))
    ea = sum(pi[c] * sum(j * p for (i, j), p in regions[c].items())
             for c in range(3))
    return [eh - mu_h, ea - mu_a]


def constrain_matrix(Q, outcome_probs, mean_home: float, mean_away: float,
                     max_iter: int = 200) -> tuple:
    """将原始矩阵 Q 约束到目标 1X2 与目标均值。

    参数：
        Q: 原始比分矩阵（list of lists，非负）
        outcome_probs: (π_H, π_D, π_A)，内部归一化
        mean_home, mean_away: 目标主客进球期望
        max_iter: root 求解最大迭代数

    返回：
        (P, info)：P 为约束后矩阵；info 含
        {"u": u, "v": v, "converged": bool, "fallback": bool,
         "max_1x2_err": float, "max_mean_err": float}

    失败时回退到 ipf_to_marginals(Q, outcome_probs)（只锁 1X2），
    并在 info["fallback"]=True 标注。
    """
    n = len(Q)
    pi = [float(outcome_probs[0]), float(outcome_probs[1]),
          float(outcome_probs[2])]
    s = sum(pi)
    if s <= 0:
        raise ValueError("constrain_matrix: outcome_probs 和非正")
    pi = [p / s for p in pi]

    info = {"u": 0.0, "v": 0.0, "converged": False, "fallback": False,
            "max_1x2_err": float("inf"), "max_mean_err": float("inf")}

    def _fallback():
        try:
            P = ipf_to_marginals(Q, tuple(pi))
        except (ValueError, ZeroDivisionError):
            # IPF也失败时：返回按目标1X2均匀分布的退化矩阵
            n = len(Q)
            P = [[0.0] * n for _ in range(n)]
            # 每个区域放一个单位质量
            placed = [False, False, False]
            for i in range(n):
                for j in range(n):
                    c = _region(i, j)
                    if not placed[c]:
                        P[i][j] = 1.0
                        placed[c] = True
            tot = sum(sum(row) for row in P)
            P = [[x / tot for x in row] for row in P] if tot > 0 else Q
        ph, pd, pa = match_probs(P)
        mh, ma = matrix_means(P)
        info["fallback"] = True
        info["max_1x2_err"] = max(abs(ph - pi[0]), abs(pd - pi[1]),
                                  abs(pa - pi[2]))
        info["max_mean_err"] = max(abs(mh - mean_home), abs(ma - mean_away))
        return P, info

    # 检查 Q 在各区域是否有质量（避免除零）
    for c in range(3):
        mass = sum(Q[i][j] for i in range(n) for j in range(n)
                   if _region(i, j) == c)
        if pi[c] > 1e-12 and mass <= 0:
            return _fallback()

    # 自研 Newton 法解 (u,v)（不依赖 scipy）
    def _res(uv):
        return _mean_residual(uv, Q, pi, mean_home, mean_away)

    try:
        (u, v), converged = _newton_solve(_res, [0.0, 0.0])
    except Exception:
        return _fallback()

    if not converged:
        return _fallback()
    # 残差检查
    res = _mean_residual([u, v], Q, pi, mean_home, mean_away)
    if abs(res[0]) > TOL_MEAN or abs(res[1]) > TOL_MEAN:
        return _fallback()

    regions, _ = _tilted_region_probs(Q, u, v)
    P = [[0.0] * n for _ in range(n)]
    for c in range(3):
        for (i, j), p in regions[c].items():
            val = pi[c] * p
            if val < 0:
                return _fallback()
            P[i][j] = val
    # 归一化（防浮点漂移）
    tot = sum(sum(row) for row in P)
    if tot <= 0:
        return _fallback()
    P = [[x / tot for x in row] for row in P]

    # 硬约束验收
    ph, pd, pa = match_probs(P)
    mh, ma = matrix_means(P)
    e12 = max(abs(ph - pi[0]), abs(pd - pi[1]), abs(pa - pi[2]))
    em = max(abs(mh - mean_home), abs(ma - mean_away))
    info.update({"u": u, "v": v, "converged": True,
                 "max_1x2_err": e12, "max_mean_err": em})
    if e12 > TOL_1X2 or em > TOL_MEAN:
        return _fallback()
    return P, info


def adaptive_max_goals(pmf_home, pmf_away, tol: float = TOL_TAIL,
                       start: int = 10, hard_cap: int = 30) -> tuple[int, float]:
    """自适应比分网格上限。

    参数为单队进球 pmf 函数（k -> P(G=k)）。
    返回 (max_goals, truncation_error)，其中 truncation_error 为
    双边遗漏质量 max(1 - Σ_{k≤M} pmf_H, 1 - Σ_{k≤M} pmf_A)。

    从 start 开始递增，直到双边遗漏质量 < tol 或达到 hard_cap。
    """
    m = start
    while m < hard_cap:
        miss_h = 1.0 - sum(pmf_home(k) for k in range(m + 1))
        miss_a = 1.0 - sum(pmf_away(k) for k in range(m + 1))
        err = max(miss_h, miss_a)
        if err < tol:
            return m, err
        m += 1
    miss_h = 1.0 - sum(pmf_home(k) for k in range(m + 1))
    miss_a = 1.0 - sum(pmf_away(k) for k in range(m + 1))
    return m, max(miss_h, miss_a)
