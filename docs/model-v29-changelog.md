# v2.9 更新日志（2026-10-09）

## 背景
GPT在BD-1.0（北单独立模型）设计评审中发现v2.8两个P0级残留bug。

## Bug 1：Shin去水仍缺booksum项（engine/market.py）
- **问题**：v2.8去掉了预归一化，但根式中仍缺 `/B`（booksum）。
  错误公式：`sqrt(z² + 4(1−z)·qᵢ²)`；正确：`sqrt(z² + 4(1−z)·qᵢ²/B)`。
- **对照**：mberk/shin 参考实现（Rust）确认根式内必须除以 `sum_inverse_odds`。
- **影响**：赔率(2,3,4)：v2.8输出(0.4776,0.3043,0.2181)，
  修正后(0.4694,0.3061,0.2245)，与参考实现误差≤1e-8。
- **验证**：176场回填，Brier 0.2086→0.2082（/3口径），未恶化，微改善。

## Bug 2：让平投影只保证≤不保证=（engine/letdraw.py, predictor.py）
- **问题**：`calibrate_handicap_1x2` 事后投影只能保证
  P(让胜)+P(让平) ≤ P(主胜)，不能保证严格等于。
  λ=(1.3,0.2)、ρ=-0.13时差1.85pp。
- **根因**：事后混合三元组再投影，破坏了分布一致性。
  且旧投影94%触发率，系统性破坏v2.5语义。
- **修复**：新增 `apply_letdraw_to_matrix()`，让平修正直接作用于比分矩阵
  的区域内重分配（BD-1.0 §6.2）：
  P_new(E) = (1−s)·P_raw(E) + s·prior，E区乘c/c0，W\E区乘(1−c)/(1−c0)。
  P(W)严格不变 ⇒ 等式天然成立（误差≤1e-12）。
- **predictor.py**：改用矩阵级修正，让球概率从修正后矩阵聚合。
- **兼容**：`calibrate_handicap_1x2` 保留（标记superseded），测试继续通过。

## walk-forward验证
| 指标 | v2.8 | v2.9 | 结论 |
|---|---|---|---|
| 胜平负Brier（176场，/3口径） | 0.2086 | 0.2082 | -0.0004，未恶化 |
| 让球等式 | ≤约束，差1.85pp | 严格=，误差≤1e-12 | 修复 |

注：让平修正现在更忠实于v2.5设计意图（旧投影94%触发，系统性缩水）。

## 测试
- 新增：test_shin_matches_reference_with_booksum（对照参考值）
- 新增：TestBug2bLetdrawMatrixEquality（6项：等式/语义/质量守恒/退化/端到端）
- 修正：test_platt_applied_in_predict阈值1e-6→1e-3（4位round误差）
- 全量：223 passed，1 skipped；3个test_beidan日期硬编码失败为已知豁免

## 版本
ENGINE_VERSION 2.8 → 2.9
