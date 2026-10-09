# 北单当前在售池脱敏导出 — 字段定义

> 期号：26103 | 导出UTC：2026-10-09T08:13:00Z | 数据源：澳客实时页面 + 本地
> 本文件为脱敏导出，不含key/凭证/服务器地址/内部路径/用户信息。

## 计数口径（两个）

| 口径 | 场数 | 说明 |
|---|---|---|
| 全池 | 193 | 26103期当前在售全部场次，含非足球 |
| 足球子池 | 187 | 仅football，可用足球模型 |
| 非足球 | 6 | 2场网球（中网女单 seq 421/422）+ 4场冰球（美职冰 seq 423-426），保留在清单中但 `football_model_eligible=false` |

**特别注意**：seq 422（中网女单，梅尔滕斯vs斯瓦泰克）虽未开赛，**绝不能用足球进球模型预测**。

## 字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| period | string | 期号（26103） |
| seq | string | 场号 |
| match_id | string | `period:seq` |
| league | string | 中文赛事名（原始） |
| home / away | string | 中文主客队名（公开） |
| sport | string | football / tennis / ice_hockey |
| event_type | string | 官方项目名（如"中网女单"、"美职冰"，足球则为联赛名） |
| football_model_eligible | bool | 是否可用足球模型做赛前预测；非足球=false，已开球=false |
| kickoff_at | string | 开球时间，+08:00 |
| kicked_off | bool | 导出时刻是否已开球（5场） |
| handicap_line | int | 澳客官方整数让球线 |
| handicap_first_seen | string/null | 让球线真实采集UTC；183/193有值，null表示无证据 |
| sp_home/draw/away | float/null | 赛前三向SP |
| sp_first_seen | object | 各向SP的真实采集UTC；180/193有值 |
| source | string | okoo（实时验证）/ local（本地） |
| source_note | string | okoo_live_verified / local_kicked_off_not_prematch / local_not_on_wdl_page |
| fetch_utc | string | 本次采集UTC |
| missing_reason | string/null | 跳过原因 |
| local_status | string | 本地预测状态 |

## 字段非空率（193场）

| 字段 | 非空数 |
|---|---|
| period/seq/match_id/league/home/away/sport/event_type | 193/193 |
| football_model_eligible/kickoff_at/kicked_off | 193/193 |
| handicap_line | 193/193 |
| handicap_first_seen | 183/193 |
| sp_home/draw/away | 182/193 |
| sp_*_first_seen | 180/193 |

## 时间范围

- 最早开球：2026-10-09 15:00+08:00
- 最晚开球：2026-10-10 23:30+08:00
- 已开球：5场（标记kicked_off=true，不可作赛前）

## first_seen语义（硬规则）

- `*_first_seen` = 我方系统实际采集到该数据的UTC时刻
- 有first_seen → 可证赛前（采集时刻 < 开球时刻）
- 无first_seen（null）→ 不可证，相关字段不可用于as-of回测
- 禁止用文件mtime、预设时间、开奖SP冒充

## 真实可用于赛前预测的场数

**183场**（football_model_eligible=true：187足球 - 4已开球 = 183）
