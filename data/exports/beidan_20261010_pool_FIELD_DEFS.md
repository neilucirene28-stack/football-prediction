# 北单2026-10-10在售池字段定义（v1）

> 抓取UTC: 2026-10-09T08:27:09Z（真实系统时间，非预设）
> 期号: 26103（澳客实时页面核验）
> 总数: 179场，seq 11-189，全足球（football_model_eligible=true）
> 来源: 澳客 BJBet WDL/WL 页面实时抓取

## 字段

| 字段 | 类型 | 说明 | 非空率 |
|---|---|---|---|
| period | string | 期号 | 179/179 |
| seq | string | 场号 | 179/179 |
| source_match_id | null | 澳客WDL页无独立mid，恒null | 0/179 |
| sport | string | football（本批全足球） | 179/179 |
| event_type | string | 联赛名原样 | 179/179 |
| home/away/league | string | 中文名 | 179/179 |
| kickoff | string | 开球时间+08:00 | 179/179 |
| handicap | float/null | 让球线（来自WL页；WDL页无让球列） | 见下 |
| handicap_first_seen | string/null | 让球线真实抓取UTC | 见下 |
| sp_wdl | object | {win,draw,lose}三向SP | 179/179 |
| sp_first_seen | string | SP真实抓取UTC | 179/179 |
| event_space | string | BJBet/WDL | 179/179 |
| football_model_eligible | bool | 是否可喂足球模型 | 179/179全true |
| fetch_utc_wdl/wl | string | 页面级抓取时间 | 179/179 |
| status | string | ok/missing_sp | 179/179 |
| missing_reason | string/null | 缺失原因 | — |

## 让球线说明

- 北单WDL页无让球列，让球线来自WL（让球胜平负）页
- WL页10-10有183场，WDL页179场，按seq匹配
- handicap_first_seen为WL页抓取时间；未匹配到的为null

## SP性质

北单胜平负SP即**让球胜平负SP**（官方玩法）。让球线为0时等同无让球。
**注意**：本批SP为实时抓取的赛前SP，非开奖SP。

## 时间证据

- 所有first_seen均为真实系统UTC时间（2026-10-09T08:27:09Z前后）
- 非预设、非mtime、非08:13人工时间
- 原始证据: `beidan_20261010_raw_evidence.txt`（页面片段+SHA256）

## 计数口径

- 全池179场（seq 11-189），全足球
- 非足球场次（421/422网球、423-426冰球）不在10-10 WDL在售池中
- 本批179场均football_model_eligible=true
