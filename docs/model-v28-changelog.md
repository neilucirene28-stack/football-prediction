# v2.8 更新日志：4个P0级bug修复（2026-10-09）

## 修复清单

### Bug1: venue缺失导致战绩静默丢弃（高）
- `engine/strengths.py::attack_defense`：无venue/非法venue记录视为中性"N"参与计算
- `estimate_lambdas`：home/away_sample改用diag实际使用记录数，口径一致
- 之前：8场无venue战绩被排除→评级退回1.0，但sample=8且degraded=False谎报正常

### Bug2: Shin去水退化为等比归一（中）
- `engine/market.py::shin_probs`：用原始倒数赔率（不预归一）求解Σpi(z)=1
- 二分法（Σpi(z)单调递减，保证收敛）；无水位时退化为等比
- 之前：q预归一使z=0成为精确解，牛顿法收敛回0，输出==等比归一
- 验证：shin(2,3,4)=(47.76%,30.43%,21.81%) vs 等比(46.15%,30.77%,23.08%)

### Bug3: 让平校准违反事件包含关系（高）
- `engine/letdraw.py::calibrate_handicap_1x2`：新增p_home/p_away参数
- 混合后若违反 P(让胜)+P(让平)≤P(主胜)（rq<0）或 P(让负)+P(让平)≤P(客胜)（rq>0)，按比例投影
- 之前：主胜3%时让平被抬到13%+，让胜+让平超出主胜11pp（不可能分布）
- 正常场次约束轻微衰减混合效果（方向保留），v2.5修正意图经walk-forward验证保留

### Bug4: 半全场边际与最终胜平负不一致（中高）
- `engine/poisson.py::half_full_1x2`：新增ft_matrix参数（IPF后矩阵）
- 对（半场比分，全场比分）联合分布做重要性重加权 w=P_IPF/P_J
- 之前：用raw λ走独立HT口径，聚合边际差4.56pp；修复后<0.01pp

## walk-forward验证（418场回填）

| 指标 | 修前(v2.7+bugs) | 修后(v2.7+fixes) | 结论 |
|---|---|---|---|
| 胜平负Brier | 0.5692 | 0.5698 | +0.0005，配对t=0.18，无统计显著性（193场变好 vs 148场变差）|
| 让球Brier | 0.6404 | 0.6391 | -0.0013，改善 |
| 比分Top2命中率 | 20.8% | 22.7% | +1.9pp，改善 |

## 测试
- 新增 tests/test_p0_bugs.py：10项回归测试（每bug≥2项）
- 全量：214 passed，1 skipped；2个test_beidan日期硬编码失败为已知豁免

## 版本
ENGINE_VERSION 2.7 → 2.8
