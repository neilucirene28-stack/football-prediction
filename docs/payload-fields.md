# predict() payload 字段说明

`engine/predictor.py::predict(payload, config)` 的输入字段。核心字段见代码；
这里记录 2026-10-06 之后新增的可选字段。

## handicap_sp（v2.5b 新增，可选）

- 含义：官方让球胜平负 SP（对应 `handicap_line` 的让球数），
  格式 `[让胜SP, 让平SP, 让负SP]`，例如 `[1.65, 4.20, 3.50]`。
- 来源：竞彩官方（如 500.com trade.500.com/jczq/ 的让球SP行）。
- 用途：背离门控 fallback。胜平负未开售（`odds` 缺失 → `p_market` 为 None）
  但有官方让球SP时，门控用「模型校准后 handicap_1x2 vs 让球SP去水概率」
  跑同一套背离逻辑（方向不一致且 gap >= divergence_gate，默认 0.15 → 触发）。
  触发时 `divergence` 含 `"market": "handicap"` 标记以便展示层区分。
- 注意：
  - `p_market`（胜平负 `odds`）存在时保持原有行为，不重复触发；
  - 无 `handicap_sp` 或无 `handicap_line` 时行为与 v2.5 完全一致；
  - 去水方法与胜平负口径一致：`shin_probs`，失败回退 `implied_proportional`；
  - 模型侧用的是 v2.5 让平校准后的值（deriv["handicap_1x2"] 的 p_home/p_draw/p_away）。

## 背景

2026-10-06 竞彩009（瑞士vs北马其顿）：模型让负(-2)84%，官方让球SP去水后
让负仅25.3%，差59个点、方向完全相反，但原门控因胜平负未开售整段跳过、
零标记。根因见 engine/predictor.py 第11b节注释。
