# v2.10 更新日志：比分分布呈现（2026-10-09）

## 背景
用户反馈比分预测每次都太小（1-1、0-0、2-1）。已验证进球均值无偏（-0.008），
不能调λ。根因：只报Poisson单点众数，天然偏小且误导性强。

## 改动（纯呈现层，零概率逻辑变更）
1. **engine/predictor.py**：
   - `top_scores`: n=3 → n=5（带概率）
   - 新增 `goal_interval_probs`：{"0-1球": p, "2-3球": p, "4+球": p}
   - 新增 `_goal_interval_probs()` helper
   - ENGINE_VERSION: 2.9 → 2.10
2. **scripts/jingcai_format.py**：
   - `format_top_scores`: 默认 n=5
   - 新增 `format_goal_intervals()`
   - `format_five_playtypes`: 6行 → 7行（新增进球区间行）
3. **tests**：更新3个测试的期望值（版本号/行数/Top5数量）

## 不碰
- 胜平负/让球/半全场/大小球的概率计算
- λ估计、IPF、Dixon-Coles、所有校准逻辑

## 验证
- 228 passed, 1 skipped（test_beidan.py硬编码日期豁免，test_soccerdata.py numpy环境问题豁免）
- 示例输出验证通过

## 示例
```
比分：**1-1**(0.132)、1-0(0.102)、2-0(0.096)、0-0(0.091)、2-1(0.089)
进球数：2-3球(0.484)，期望2.57球
进球区间：0-1球(0.259)、2-3球(0.484)、4+球(0.257)
```
