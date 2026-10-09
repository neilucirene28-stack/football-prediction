# 北单008场form记录 + 4场SP证据 — 字段定义（2026-10-09）

> 纯只读数据交接，不开发北单模型，不出预测。
> id_verified=false、observation_only=true 保持。

## 文件

| 文件 | 行数 | 说明 |
|---|---|---|
| `beidan_008_form_20261009.jsonl` | 20 | 柏太阳神/神户胜利船各10场赛前form |
| `beidan_008_team_mapping.json` | — | 球队名称对应表 |
| `beidan_4matches_sp_evidence.jsonl` | 4 | seq 7/8/9/10 SP抓取证据 |

## form记录字段

| 字段 | 说明 | 非空率 |
|---|---|---|
| team_cn | 中文队名 | 20/20 |
| team_source_name | 源站原始名 | 20/20 |
| date | 比赛日期 | 20/20 |
| venue | home/away | 20/20 |
| opponent | 对手 | 20/20 |
| score | 比分 | 20/20 |
| half_score | 半场比分 | 20/20 |
| competition | 赛事 | 20/20 |
| source | okooo | 20/20 |
| source_collected_at | 源文件采集UTC | 20/20 |
| okooo_mid | 澳客比赛ID | 20/20 |
| id_verified | false（未人工核验） | 20/20 |
| observation_only | true | 20/20 |

最早：2026-08-08，最晚：2026-10-03（均在008开球10-09 18:00前）

## SP证据字段

| 字段 | 说明 | 非空率 |
|---|---|---|
| period/seq/match_id | 期号/场号/ID | 4/4 |
| handicap_line + first_seen | 让球线+真实采集UTC | 4/4 |
| sp_home/draw/away + first_seen | 三向SP+真实采集UTC | 4/4 |
| sp_nature | 让球胜平负SP说明 | 4/4 |
| fetch_utc | 抓取UTC | 4/4 |

## 球队映射

8支球队在team_id_map.json中**均无记录**。ESPN/API-Football源ID来自先前审计，**未经人工核验**。

## 时间证据

- form来源：`data/daily/2026-10-09/okooo_history.json`（文件mtime为采集时间）
- SP来源：澳客实时抓取，`fetch_utc=2026-10-09T08:13:00Z`
