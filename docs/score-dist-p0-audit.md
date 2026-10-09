# P0基线审计（GPT比分分布替换，2026-10-09）

## -0.008偏差来源确认

**结论：-0.008是最终IPF矩阵的期望总进球偏差，不是原始λ的。**

证据链：
- 572场回填：模型期望总进球均值 3.067 vs 实际 3.075，偏差 -0.008
- "期望总进球"取自预测输出的 `expected_total_goals(matrix_cal)`，
  其中 `matrix_cal = ipf_to_marginals(matrix, p_final)`（B深修后所有衍生项统一从校准后矩阵计算）
- 原始λ (lam_h + lam_a) 的偏差未单独记录，但IPF区域缩放会改变均值：
  - IPF只锁定1X2三区域概率，不锁定区域内进球分布
  - 因此最终矩阵的 E[主队进球] ≠ lam_h，E[客队进球] ≠ lam_a（一般有漂移）

**P0约束层的意义**：`constrain_matrix(Q, π, μ_H, μ_A)` 同时锁定1X2和双队均值，
其中 (μ_H, μ_A) 取自当前IPF基准矩阵的实际均值。P1换分布后，
用同一组 (π, μ_H, μ_A) 做约束，保证"均值不变"指的是最终输出均值不变，
而不只是输入λ不变。

## v2.10 score_matrix生成逻辑（当前基线）

```
1. lam_h, lam_a ← strengths.py（收缩先验+弱赛事封顶）
2. matrix = score_matrix(lam_h, lam_a, rho=-0.13)
   - 独立Poisson × Dixon-Coles低比分修正（00/01/10/11四格）
   - 网格：每队0-10球固定，归一化
3. p_model = match_probs(matrix)
4. ensemble(模型/市场/Elo) + Platt → p_final
5. matrix_cal = ipf_to_marginals(matrix, p_final)
   - 三区域乘法缩放，锁定1X2= p_final
   - 区域内相对概率不变（最小KL调整）
6. 所有衍生项从 matrix_cal 聚合：
   top_scores(Top5) / handicap_1x2 / total_goals / over_under /
   asian / btts / half_full_1x2(v2.8重加权)
```

## P0交付物

- `engine/score_constrain.py`：constrain_matrix() + adaptive_max_goals()
  - 指数倾斜：P_ij = π_c · Q_ij·e^(u·i+v·j) / Σ_{(a,b)∈c} Q_ab·e^(u·a+v·b)
  - 自研Newton法解(u,v)，不依赖scipy（环境scipy/numpy不兼容）
  - 失败回退到IPF，保证总有合法输出
- `scripts/validate_constrain_p0.py`：5个case验证复现精度
- `tests/test_score_constrain.py`：5项单测

## P0验证结果

| case | max格差 | 1X2误差 | 均值误差 | 状态 |
|---|---|---|---|---|
| 5个Poisson case | ≤2.78e-17 | ≤1.11e-16 | ≤4.44e-16 | 全部OK，零回退 |
| 非平凡（均值+10%） | — | 0.00e+00 | 4.44e-16 | 倾斜生效，u=0.14 v=0.05 |

自适应网格：λ=1.5→10球，λ=3.0→14球，λ=5.0→19球，截断误差<1e-6。
