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
| **six_play_vector** | object | 完整六玩法概率向量（见下）。**score_top5仅为展示字段，不作为概率依据** |
| **score_matrix_full** | [[float]] | 全比分矩阵（n×n，每个格子概率），或由其聚合的31类分布。六玩法所有概率必须从同一矩阵聚合 |
| model_version | string | 模型版本（如 `2.10`） |
| handicap_line | float/null | 让球线及来源 |
| sp_snapshot | object/null | 当时SP（如有），**必须带采集时间戳**，缺失不反推 |
| data_completeness | object | 各数据源是否齐全：`{source: true/false}` |
| skipped | bool | 是否因数据不足跳过 |
| skip_reason | string/null | 跳过原因 |

## 六玩法完整概率向量定义（v2，GPT审计修正）

> **核心原则**：Top5比分不是完整概率向量。六玩法的每个结果都必须有概率，
> 且所有概率必须从**同一比分矩阵**聚合，概率和为1。

### 1. wdl（胜平负）
`{胜: p, 平: p, 负: p}` —— 校准后最终值，p_home/p_draw/p_away。

### 2. handicap_wdl（让球胜平负）
`{让胜: p, 让平: p, 让负: p, handicap_line: n}` —— 从让平修正后矩阵聚合，
必须满足 P(让胜)+P(让平) ≡ P(主胜)（v2.9已保证）。

### 3. score（比分）——北单官方31类
必须保存**完整31类分布**（含胜其他/平其他/负其他），二选一：
- **选项A**：全比分矩阵（n×n，每个格子概率），31类由消费方聚合
- **选项B**：直接存31类概率

北单官方31类（福建体彩《中国足球彩票单场竞猜比分游戏规则》2024-04-01第六条；
25类是下半场比分，不可作全场）：
- **胜13类**：1-0, 2-0, 2-1, 3-0, 3-1, 3-2, 4-0, 4-1, 4-2, 5-0, 5-1, 5-2, **胜其他**
- **平5类**：0-0, 1-1, 2-2, 3-3, **平其他**
- **负13类**：0-1, 0-2, 1-2, 0-3, 1-3, 2-3, 0-4, 1-4, 2-4, 0-5, 1-5, 2-5, **负其他**

"其他"类 = 该方向下超出所列具体比分的所有比分概率之和。
`score_top5` 降级为**展示字段**，不作为概率依据、不进入回测。

### 4. total_goals（总进球）
8类：`{0: p, 1: p, 2: p, 3: p, 4: p, 5: p, 6: p, "7+": p}` —— 从比分矩阵按总进球聚合。

### 5. half_full（半全场）
9类：`{胜胜, 胜平, 胜负, 平胜, 平平, 平负, 负胜, 负平, 负负}` —— 各组合概率。

### 6. ou（上下单双）
4类：`{上单, 上双, 下单, 下双}` —— 上/下按总进球≥3为上（以北单官方口径为准），单/双按总进球奇偶。

### 完整性校验
每个玩法的概率和必须为1（容差1e-6）。写入时自动校验，不通过则拒绝写入并报错。

## 时间证据硬规则（v2新增，P0审计强化）

1. `generated_at`、各源`available_at`、`asof`、`kickoff` **必须有真实时间证据**。
2. 缺来源时间 → 该场自动标 `observation_only=true`，**不可入赛前回测**。
3. **禁止预设时间**（如填14:30:00冒充生成时刻）。
4. **禁止mtime冒充**（文件写入时间≠数据采集时间，前车之鉴见1665场导出修正）。
5. `generated_at` 必须是引擎实际跑完该场预测的时刻，精确到秒。
6. **无时区时间戳一律拒绝**（不强行按UTC解释）。
7. **时间顺序**：`available_at <= asof <= generated_at < kickoff`，违反任一项即`observation_only=true`。
   - null来源不被忽略：任一源为null即证据不足。
8. 概率校验：每类和为1（容差1e-6），每项0<=p<=1且有限（非NaN/Inf）。

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

- [x] 规范文档（本文，v2已修正六玩法定义+时间证据硬规则）
- [x] predictor.py：beidan模型输出score_matrix_full（竞彩流程 untouched）
- [x] `engine/beidan_snapshot.py`（append-only写入+rerun链+时间校验+概率和校验）
- [x] `scripts/beidan_chunk1_run.py` 调用（快照失败不阻断预测）
- [x] `tests/test_beidan_snapshot.py`（8项测试）
- [ ] 回测消费方的时间门控校验（待BD-1实现时做）

## v3 审计修正（GPT复核，2026-10-09）

1. **SP时间证据**：`sp_snapshot.collected_at` 必须有SP源真实采集证据；
   无证据时置 null 并标 `observation_only=true`。**严禁**用战绩抓取完成时刻
   （t_data_ready）替代。
2. **让球线缺失**：`handicap_line`/`handicap_wdl` 缺失时为 null，
   不默认成0，不参与预测。
3. **并发安全**：O_APPEND + fcntl独占锁 + fsync；旧记录路径/字节不变。
   `rerun_of` 在锁内重算，指向实际存在的前一条。
4. **合成样本**：`synthetic_sample=true` 的记录强制 `observation_only=true`。
5. **半全场边际**：9项对全场1X2的边际必须一致（容差1e-6），否则拒绝写入。
