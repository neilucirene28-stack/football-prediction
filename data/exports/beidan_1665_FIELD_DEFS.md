# 北单1665场历史赛果脱敏导出 — 字段定义

文件：`data/exports/beidan_1665_results.jsonl`（1665行，每行一场）
源：`beidan_backtest_periods.json`（8期：26092/26093/26094/26095/26096/26097/26098/26101，2026-09-04 ~ 10-05）
导出时间（UTC可信时钟）：2026-10-09T06:37:06Z

## 字段

| 字段 | 类型 | 非空率 | 说明 |
|---|---|---|---|
| lottery_no | string | 100% | 期号 |
| seq | string | 100% | 场号（期号内） |
| league | string | 100% | 联赛中文名（原样，未规范化） |
| home / away | string | 100% | 公开队名 |
| kickoff | string | 100% | 开球时间，`YYYY-MM-DD HH:MM+08:00` |
| full_score | string | 100% | 常规时间全场比分，如 `2-0` |
| half_score | string | 100% | 半场比分，如 `0-0` |
| handicap | float/null | 36.8% | 让球线（仅613场有值） |
| handicap_collected_at | null | — | 让球线采集时间**未证**，恒为null |
| result_available_at | null | — | 原始赛果发布时间**未知**，恒为null |
| norm_league_id | null | — | 规范化赛事ID缺失，恒为null |
| league_family | null | — | 赛事族缺失，恒为null |
| source | string | 100% | `beidan_backtest_periods` |
| missing_handicap | bool | 100% | 让球线是否缺失 |
| provenance_unverified | bool | 100% | 恒为true：采集链路未经核验 |
| usage | string | 100% | `score_distribution_only` |
| **verified_at** | string | 100% | `2026-10-09T06:37:06Z`（见下） |

## ⚠️ 时间语义（必读，防前视偏差 lookahead bias）

**`verified_at` 的含义**：这批赛果**首次被我方系统确认**的时间（2026-10-09T06:37:06Z，UTC可信时钟）。
它**不是**赛果实际发生/发布的时间——`result_available_at` 为 null 正是因为原始发布时间未知。

**硬规则（任何使用者必须遵守）：**

1. **只有 `asof >= verified_at` 的未来比赛**，才允许把这1665场赛果作为训练历史使用。
   也就是说：只有在 2026-10-09T06:37:06Z **之后**开球的比赛，才能用这批数据训练/回测。

2. **绝不能**把 `verified_at` 倒填到9月/10月初，用这批赛果做历史 walk-forward。
   这些比赛实际发生在9月4日~10月5日，但我们直到10月9日才确认拿到它们。
   如果你在9月20日的"赛前"回测里用了9月25日才确认的赛果——那就是**前视偏差**，结果无效。

3. 本批数据的合法用途只有：**比分分布描述统计**（如让平率、全场/半场比分分布、进球数分布）。
   不可做严格 as-of 回测，不可做训练样本（除非满足规则1的时间条件）。

**一句话版本**：数据拿到手的时间 ≠ 数据产生的时间；只能用"拿到手之后"的比赛去消费它。

## 未导出项

- 任何SP字段（`rq_sp`/`score_sp`/`goals_sp`/`half_full_sp`/`ou_sp`）——疑似赛后开奖SP，严禁当赛前市场信号
- 密钥、服务器地址、内部路径、用户信息——敏感扫描零命中
