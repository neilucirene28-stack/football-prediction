# 北单4场审计数据字段定义（2026-10-09）

> 4场未开球比赛：seq 7/8（18:00）、seq 9/10（23:00），均为26103期。
> 纯只读数据，不做预测。GPT负责预测。

## 字段定义

| 字段 | 说明 |
|---|---|
| match_id | 期号:场号 |
| period/seq | 期号/场号 |
| league_cn | 中文联赛名（原始） |
| home_cn/away_cn | 中文主客队名（原始） |
| home_en/away_en | 英文队名（源数据原始） |
| sport/event_type/event_space | 项目/赛事类型/项目空间标识 |
| kickoff_at | 开球时间（+08:00） |
| kicked_off | 是否已开球 |
| handicap_line | 官方让球线（整数） |
| handicap_first_seen | 让球线真实采集UTC（2026-10-09T08:13:00Z） |
| sp_home/sp_draw/sp_away | 北单官方胜平负SP |
| sp_first_seen | SP真实采集UTC |
| sp_nature | SP性质：beidan_wdl_with_handicap（北单官方胜平负SP，含让球线） |
| home_id_espn / away_id_espn | ESPN源球队ID（**未经人工核验**） |
| home_id_apifootball / away_id_apifootball | API-Football源球队ID（**未经人工核验**） |
| id_verified | 是否经人工核验（**全部false**） |
| form_data_available | 是否有近况数据 |
| form_source/form_available_at | 近况来源/采集时间 |
| football_model_eligible | 是否可进足球模型 |
| observation_only | 是否仅观察（**全部true**） |

## 数据完整度（4场）

| 项目 | seq 7 | seq 8 | seq 9 | seq 10 |
|---|---|---|---|---|
| 让球线+first_seen | ✅ | ✅ | ✅ | ✅ |
| SP+first_seen | ✅ | ✅ | ✅ | ✅ |
| 源ID（未核验） | ESPN | ESPN | API-FB | API-FB |
| 人工核验ID | ❌ | ❌ | ❌ | ❌ |
| 近况数据 | ❌ | ✅(10场) | ❌ | ❌ |

## 关键说明

1. **SP性质**：北单官方胜平负SP即让球胜平负SP（含让球线）。让球线为0时等于无让球SP；seq 9让球线-1，其SP 2.31/4.05/3.1为让球SP。
2. **ID未核验**：8支球队在team_id_map.json中均无记录；ESPN/API-Football ID为源数据自带，未经人工核验。
3. **全部observation_only=true**：无人工核验ID，其中3场无近况数据。
4. **澳客采集归档**：`data/daily/2026-10-09/beidan_sp.json`（source=okooo），event_space见本文件。
