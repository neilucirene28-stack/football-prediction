# 北单预测不可覆盖快照 — 字段规范（v1）

> 目标：每次北单预测生成时，逐场写入一条**不可覆盖（append-only）**快照，
> 记录完整的赛前状态，供日后 as-of 回测与消融使用。
> 现有111条 unverified 记录**不要回填**成训练样本（见1665场导出的时间语义规则）。

## 快照粒度

每场比赛一条记录，key = `{lottery_no}:{seq}`。

## 必填字段

| 字段 | 类型 | 说明 |
|---|---|---|
| lottery_no | string | 期号 |
| seq | string | 场号 |
| match_id | string | 赛事ID（规范化后；暂无则填源站原始ID并标注） |
| league | string | 联赛名 |
| home / away | string | 队名 |
| kickoff | string | 开球时间，带时区 |
| **generated_at** | string (ISO8601) | **本场预测生成的时刻**（引擎跑完的时间，不是文件写入时间） |
| **available_at** | object | **各源数据可用时间**：`{source_name: ISO8601}`，如 `{"espn": "...", "7m": "...", "okooo_sp": "..."}`；某源缺失则该key为null并记原因 |
| **lambda_home / lambda_away** | float | 赛前 λ_H / λ_A（进入比分矩阵前的值） |
| **p_1x2** | [float×3] | 完整胜平负概率向量（校准后最终值） |
| **six_play_vector** | object | 完整六玩法向量：`{wdl, handicap_wdl, score_top5, total_goals, half_full, ou}`，每个玩法为概率分布（非单点） |
| model_version | string | 模型版本（如 `2.10`） |
| handicap_line | float/null | 让球线及来源 |
| sp_snapshot | object/null | 当时SP（如有），**必须带采集时间戳**，缺失不反推 |
| data_completeness | object | 各数据源是否齐全：`{source: true/false}` |
| skipped | bool | 是否因数据不足跳过 |
| skip_reason | string/null | 跳过原因 |

## 不可覆盖规则

1. 快照写入后**永不修改、永不删除**。同一 `{lottery_no}:{seq}` 如需重跑，
   写新记录并加 `rerun_of` 指向原记录、注明原因。
2. `generated_at` 必须是**真实生成时刻**，禁止预设、禁止填文件mtime冒充。
3. 回测消费时：只允许 `asof >= generated_at` 的快照进入训练集；
   评估某场比赛时，只允许 `generated_at < kickoff` 的快照。
4. 快照与赛果结算分离：赛果到来时另写结算记录，通过 `{lottery_no}:{seq}` 关联，
   不得回写快照。

## 与1665场历史导出的关系

1665场历史赛果（`verified_at=2026-10-09T06:37:06Z`）**不是**快照，
没有 `generated_at`/`available_at`/λ/六玩法向量，**不能**转换为快照格式回填。
它只能做比分分布描述（详见 `data/exports/beidan_1665_FIELD_DEFS.md` 的时间语义章节）。

## 实施状态

- [ ] 规范文档（本文）
- [ ] predictor.py 快照写入代码
- [ ] append-only 存储（文件或表）
- [ ] 回测消费方的时间门控校验
