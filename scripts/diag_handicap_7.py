"""让平系统性偏低：诊断脚本（只诊断，不调参）。

背景：2026-09-30 复盘（周二 15 场，v2.3 引擎）：14 场有让球盘，
7 场打出"让平"（净胜球恰好等于盘口），模型给的让平概率普遍只有 11%–27%。

A. 二项检验：若模型无偏，14 场期望命中 ≈ 14 × 0.19 ≈ 2.7 场，实际 7 场。
B. 机制演示：λ 高估 → 净胜球分布过宽 → P(净胜恰=盘口)被压低、P(穿盘)虚高。
   用真实管线（estimate_lambdas → score_matrix → handicap_1x2）对比
   shrink_prior=0.0（v2.3）vs 3.0（v2.4）。

结论：让平问题是"进球数系统性高估"的下游症状，v2.4 的结构性收缩
正好打在根因上。样本 <100 不拟合任何参数；校准靠 scoreboard.py 里
已有的 handicap_brier 追踪继续积累。
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.strengths import estimate_lambdas
from engine.poisson import score_matrix, handicap_1x2


def binom_tail(n, k, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
               for i in range(k, n + 1))


def part_a():
    print("== A. 二项检验（14 场有让球盘，7 场打出让平） ==")
    print("复盘记录：模型让平概率普遍 11%–27%。")
    for p in (0.19, 0.27):
        exp = 14 * p
        tail = binom_tail(14, 7, p)
        print(f"  假设平均 P(让平)={p:.2f}：期望命中 {exp:.1f} 场，"
              f"P(≥7场) = {tail:.4f}")
    print("  → 即使按 27% 的上界算，7/14 也是小概率事件：系统性低估，非运气。")
    print("  （注：用的是复盘报告的概率区间，非逐场精确值；精确校准"
          "靠 scoreboard.handicap_brier 继续积累。）")


def part_b():
    print("\n== B. 机制演示：同一份战绩，v2.3 vs v2.4 的让球概率 ==")
    fav = [{"gf": 2, "ga": 1, "venue": "H"} for _ in range(8)]
    weak = [{"gf": 1, "ga": 2, "venue": "A"} for _ in range(8)]
    for sp, tag in ((0.0, "v2.3 无收缩"), (3.0, "v2.4 收缩")):
        lh, la, _ = estimate_lambdas(fav, weak, 2.70, home_adv_factor=1.12,
                                     shrink_prior=sp)
        m = score_matrix(lh, la, rho=0.0)
        h, d, a = handicap_1x2(m, -2)
        print(f"  {tag}: λ=({lh:.2f}, {la:.2f})  "
              f"让胜 P(净胜≥3)={h:.3f}  让平 P(净胜=2)={d:.3f}  让负={a:.3f}")
    print("  → 收缩把 λ 拉回均值 → 净胜分布收窄 → 让平概率上升、穿盘概率下降。")


if __name__ == "__main__":
    part_a()
    part_b()
