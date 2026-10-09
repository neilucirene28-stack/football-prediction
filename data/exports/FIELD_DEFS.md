# 北单数据导出字段定义（FIELD_DEFS）

生成时间：2026-10-09
期号：26102 / 26103
模型：26102用v2.5，26103用v2.9

## ⚠️ 时间戳修正记录（2026-10-09，GPT核验发现）

- 原 `collected_at` 全为 `2026-10-09T14:30:00+08:00`，系原始JSON内预设的 `generated_at` 值，**并非真实写入时间**
- 原始预测文件 `data/predictions/2026-10-09-beidan.json` 实际mtime：`2026-10-09T14:20:16+08:00`（UTC 06:20:16）
- 已修正为文件实际写入时间，并新增 `provenance_unverified=true` 标记
- **结论**：这批数据仅作观察（observation_only），不可用于 as-of 回测

## 文件清单

| 文件 | 行数 | 说明 |
|---|---|---|
| `beidan_26103_shadow.jsonl` | 111 | 26103期status=ok的预测，**影子预测，未结算，不可作回测** |
| `beidan_26103_skipped.jsonl` | 82 | 26103期跳过场次清单 |
| `beidan_26102_summary.jsonl` | 31 | 26102期有预测场次，**来自markdown报告解析，非原始payload** |

## 193场 vs 111场：数据结构差异

- 26103在售共193场（`data/predictions/2026-10-09-beidan.json` 的 matches 数组）
- 其中111场 `status=ok`（有完整预测向量）→ `beidan_26103_shadow.jsonl`
- 其中82场 `status=skipped`（数据不足，按铁律不编）→ `beidan_26103_skipped.jsonl`
- 111+82=193，无遗漏

## 字段定义

### beidan_26103_shadow.jsonl

| 字段 | 类型 | 说明 |
|---|---|---|
| match_id | string | `{lottery_no}-{seq}`，如 "26103-3" |
| lottery_no | string | 期号 "26103" |
| seq | string | 北单场次序号 |
| league | string | 联赛中文名 |
| home / away | string | 主客队名（比赛必需信息，保留） |
| kickoff | string | 开球时间（北京时间） |
| collected_at | string | **已修正**：原始预测文件实际写入时间 `2026-10-09T14:20:16+08:00`（原14:30:00为预设值，已作废） |
| provenance_unverified | bool | true：采集时间未经核验，仅为文件写入时间 |
| provenance_note | string | 说明文字：collected_at为文件写入时间，非经核验的赛前生成时刻 |
| usage | string | "observation_only"：仅作观察 |
| as_of_backtest | bool | false：不可用于 as-of 回测 |
| form_source | string | 近况数据源（7m/espn等） |
| n_home_recent / n_away_recent | int | 主客近况场次 |
| sp_wdl | object | 澳客胜平负SP {胜,平,负}；缺项时对应值为null |
| sp_incomplete | bool | true=SP向量不完整（64/111场） |
| market_normalization | bool | sp_incomplete时为false：缺项不做市场归一 |
| handicap | float | 官方让球数 |
| p_home / p_draw / p_away | float | 模型胜平负概率 |
| missing_flag | bool | false（跳过的在另一文件） |
| prediction_status | string | "shadow" |
| note | string | 标注说明 |

### beidan_26103_skipped.jsonl

| 字段 | 说明 |
|---|---|
| match_id / lottery_no / seq / league / home / away / kickoff | 同上 |
| missing_flag | true |
| skip_reason | 跳过原因（如"7m限流"、"titan007子域被拒"） |

### beidan_26102_summary.jsonl

| 字段 | 说明 |
|---|---|
| 同上基础字段 | match_id="26102-{seq}" |
| top_score / top_score_prob | 首选比分及概率 |
| expected_goals | 期望总进球 |
| parse_ok | markdown解析是否成功 |
| collected_at | 报告日期（精确采集时间未留档） |

## 明确缺失的字段（不编造）

1. **lambda（λ_H/λ_A）**：引擎未存储，未导出
2. **最终比分**：26103未开赛，无比分；26102比分在复盘文件 `data/reviews/done/beidan-26102.json`（未脱敏导出）
3. **26102精确采集时间**：只有报告日期2026-10-06
4. **26102原始payload**：只有markdown报告，无结构化JSON

## 未来真实预测必须记录的字段（GPT要求）

| 字段 | 说明 |
|---|---|
| generated_at | 预测实际生成时间（写入时刻，非预设） |
| available_at_{source} | 每个数据源的实际可用时间（如available_at_7m、available_at_espn） |
| lambda_home / lambda_away | 赛前λ_H/λ_A（当前引擎未存储，需补） |
| p_home / p_draw / p_away | 完整1X2概率（已有） |
| engine_version | 模型版本（已有） |
| match_id | 赛事ID（已有，{lottery_no}-{seq}） |
| sp_snapshot_at | SP快照的实际采集时间 |

## 脱敏说明

- 队名保留（比赛必需，公开信息）
- 不含任何API key、凭证、服务器地址、用户信息
- 已做敏感字段扫描（见commit记录）
