# 北单179场覆盖账本字段定义

文件：`data/exports/beidan_179_coverage_20261010.jsonl`（179行）

## 字段
| 字段 | 说明 |
|---|---|
| period | 期号（26103） |
| seq | 场次序号 |
| beidan_home/away | 北单中文主客队 |
| beidan_kickoff | 北单开球时间（+08:00） |
| beidan_kickoff_utc | 换算UTC |
| beidan_league | 北单中文联赛 |
| provider | espn/apifootball/null |
| provider_event_id | provider事件ID |
| provider_home_id/away_id | provider球队ID（null=未提取） |
| provider_kickoff | provider开球UTC |
| match_status | matched_prior（bb0c436严格匹配）/unmatched |
| uncovered_reason | 未覆盖原因或候选说明 |
| af_candidate_ids | AF候选fixture_id列表（如有） |

## 覆盖统计
- matched_prior: 5场（seq 18/19/22/23/24，主客+UTC严格一致）
- ESPN scoreboard候选（同开球时间，需人工核对中文名）：约144场
- AF候选（J2/K联赛/捷甲）：15场
- 硬缺口（ESPN无此联赛+AF无覆盖）：爱甲、波兰甲、葡甲、比乙、冰岛超、爱足总杯
- ESPN有代码但0事件：奥乙、瑞士甲、丹甲、芬甲、瑞典甲、罗甲、爱超、芬超

## 纪律
- 不猜球队映射，不用相似名字自动匹配
- 候选需Codex人工核对
- id_verified全部false
