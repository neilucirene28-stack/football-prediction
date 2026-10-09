# beidan_teams_form_20261009.jsonl 字段定义

## 数据来源
- ESPN隐藏API（jpn.1/ger.2/nor.1/den.1/swe.1球队赛程）：免费，无配额限制
- thesportsdb（低级别联赛球队）：免费key=3，每队仅返回1场最近比赛

## 采集时间
- ESPN J1: 2026-10-09T09:34:03Z (UTC)
- ESPN欧洲: 2026-10-09T09:34:46Z前后 (UTC)
- thesportsdb: 2026-10-09T09:34:46Z (UTC)

## 覆盖
- 总记录数: 269
- 007/008 (4支J1球队): 112场 (ESPN, 每队28场)
- 明日最早20场中的欧洲球队 (10支): 128场 (ESPN)
- 明日最早20场中的低级别球队 (29支): 29场 (thesportsdb, 每队1场)
- 未找到: Pk-35万塔 (thesportsdb无记录)

## 字段定义

| 字段 | 类型 | 说明 | 非空率 |
|------|------|------|--------|
| provider | string | 数据来源: espn/thesportsdb | 269/269 (100%) |
| provider_match_id | string | 来源方比赛ID (ESPN event_id / thesportsdb idEvent) | 269/269 (100%) |
| team_cn | string | 中文队名 (查询用) | 269/269 (100%) |
| home | string | 主队名 (来源方原始) | 269/269 (100%) |
| away | string | 客队名 (来源方原始) | 269/269 (100%) |
| score_home | string | 主队比分 | 269/269 (100%) |
| score_away | string | 客队比分 | 269/269 (100%) |
| kickoff | string | 开球时间 (ISO8601, 带时区) | 269/269 (100%) |
| league | string | 联赛代码/名称 | 269/269 (100%) |
| receipt_utc | string | 实际收到数据的UTC时间 | 269/269 (100%) |
| id_verified | boolean | 人工核验状态 | 269/269 (100%, 全false) |
| team_espn_id | string | ESPN球队ID (仅ESPN记录) | 240/269 |
| home_espn_id | string | ESPN主队ID (仅ESPN记录) | 240/269 |
| away_espn_id | string | ESPN客队ID (仅ESPN记录) | 240/269 |
| team_thesportsdb_id | string | thesportsdb球队ID (仅TSDB记录) | 29/269 |

## 重要声明

1. **id_verified全为false**: 所有球队ID均未经人工核验，仅为AI比对/来源方原始ID，不可作为人工审核证据。
2. **thesportsdb每队仅1场**: 低级别联赛球队仅能获取1场最近比赛，不满足"各10场优先"。
3. **Pk-35万塔缺失**: thesportsdb中未找到该队，记录为null。
4. **比分主客方向**: 来自来源方原始competitors结构的homeAway字段。

## 原始归档
- data/exports/raw/espn_j1_20261009.json (SHA256: 383f45557c7f3645...)
- data/exports/raw/espn_eu_20261009.json (SHA256: 2773783aac69aa1a...)
- data/exports/raw/tsdb_low_20261009.json (SHA256: 8c027d910fd41a3f...)

