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

## af_pred（v2.6 新增，可选）

- 含义：第三方独立模型三向概率（API-Football /predictions），
  格式 `[pH, pD, pA]`（0-1），例如 `[0.10, 0.45, 0.45]`。
- 来源：`collector/sources/apifootball_predictions.py::get_predictions(fixture_id)`；
  daily_fetch.py 第4节每天批量拉取在售场次，存
  `data/daily/YYYY-MM-DD/afb_predictions.json`（theopenmodel替代，2026-10-08起）。
- 用途：独立模型信号分歧检测（模型 vs 模型，非市场分歧）。
  在胜平负校准后（p_home/p_draw/p_away 终值）比较：引擎首选方向 vs AF首选方向，
  不一致且 gap >= divergence_gate（默认0.15）→ 触发，沿用B补丁门控口径：
  信心降一档、conf_score-15、risk+15。
  触发时 `divergence` 记 `{"signal": "af_model", "model_direction": ...,
  "af_direction": ..., "gap": ...}`，risk_factors 加"独立模型信号分歧（AF）"，
  notes 文案与市场分歧区分（"独立模型信号分歧（本模型看X、AF模型看Y）"）。
- 注意：
  - 与市场门控（含让球fallback）互斥：市场门控已触发时不再重复触发；
  - 方向一致或 gap<0.15 时静默；无 `af_pred` 时行为与 v2.5b 完全一致；
  - `af_pred` 残缺/非法/全零时不崩溃、不触发（内部归一化防脏数据）。
- predict 脚本组装（2026-10-08补）：
  ```python
  from apifootball_predictions import load_afb_lookup
  lk = load_afb_lookup("data/daily/2026-10-08")  # {(board_home, board_away): [pH,pD,pA]}
  # board_home/board_away 是 okooo 中文名；与 500.com 名有细微差异时做模糊匹配
  key = (home_zh, away_zh)  # 或模糊匹配后的键
  if key in lk:
      payload["af_pred"] = lk[key]
  ```
  只有无歧义对齐的条目才有 board_home/board_away（同联赛同时开球的多场在
  daily_fetch 侧跳过中文名，`ambiguous_skipped` 计数）；查不到就静默不传。
