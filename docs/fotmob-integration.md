# FotMob 接入说明（2026-10-06）

## 是什么
FotMob 非官方 JSON API（`www.fotmob.com/api/data/`），免 key、免登录、纯 JSON。
定位：**北单常客低级别联赛的赛程/比分/积分榜/球队近况主力新源**，补 ESPN
不覆盖 J2/J3、K2/K3、挪甲/挪乙、瑞典甲/瑞典乙、巴西乙的短板。

## 模块
`collector/sources/fotmob.py`，沿用 `_http.fetch_with_retry`（curl 子进程 + CA bundle）。

| 函数 | 端点 | 返回 |
|---|---|---|
| `get_standings(联赛)` | `/leagues?id=&ccode3=` | 积分榜：played/wins/draws/losses/scoresStr→gf/ga/pts/rank |
| `get_fixtures(联赛)` | 同上 | 全赛季赛程：allMatches，含已赛比分/未赛开球时间 |
| `get_matches(date)` | `/matches?date=YYYYMMDD` | 当日 59 联赛赛程，自动过滤到本模块 9 联赛（注意：该端点 `id` 为分组 id，真实联赛 id 在 `primaryId`） |
| `get_team(fotmob_id)` | `/teams?id=` | 近况 teamForm / 全部赛程比分 / 阵容（休赛期阵容可为 null） |
| `get_match(match_id)` | `/match?id=` | 单场比分/状态 |

联赛 ID（2026-10-06 实测全部 200）：

| 中文名 | id | ccode3 |
|---|---|---|
| J2联赛 | 8974 | JPN |
| J3联赛 | 9136 | JPN |
| K2联赛 | 9116 | KOR |
| K3联赛 | 9537 | KOR |
| 挪甲 | 203 | NOR |
| 挪乙 | 204 | NOR |
| 瑞典甲 | 168 | SWE |
| 瑞典乙 | 169 | SWE |
| 巴西乙 | 8814 | BRA |

## 每日管线
`scripts/daily_fetch.py` 第 13 节：按 `FOTMOB_LOW_LEAGUES` 逐联赛
try/except 隔离抓取，输出 `data/daily/YYYY-MM-DD/fotmob_lowleagues.json`
（含各联赛 standings + fixtures 全量、当日赛程）。
抓到积分榜后自动按需补 `data/team_id_map.json`（`fotmob_id` 字段），
走 `team_names.normalize` 归一化，不写一次性硬编码。

## 已知短板（诚实记录）
1. **单场无赔率**：`odds` 字段恒为 null，本模块不解析赔率；赔率仍靠
   The Odds API / Titan007 / OddsHarvester。
2. **非官方接口**：随时可能变更/限流；模块内部请求间隔 ≥1s，
   失败只记 FAIL 不中断其他源；建议在 cron 侧加失败告警。
3. 低级别 xG 仍无新增源；低级别伤停仍以 transfermarkt 为主。

## OddsHarvester 批量回填
`scripts/backfill_oddsharvester.py`：historic 模式批量回填 OddsPortal
低级别联赛历史赔率（1x2/亚盘/大小球），输出到
`data/backfill/oddsharvester/<联赛>/<赛季>/<玩法>.json|csv`。
按（联赛×赛季）组合分批跑，已存在输出默认跳过（`--no-resume` 强制重跑）；
`--preview-only --max-pages N` 做小样本试跑。

**环境限制（2026-10-06 实测）**：Hatch VM 上 Playwright/Chromium 走出口
代理一律 `ERR_EMPTY_RESPONSE`（curl 正常，连 example.com 都不通），
属代理指纹拦截，非脚本问题。脚本已内置 `--ignore-certificate-errors`
和环境变量代理读取；Hatch VM 上若仍失败，改去阿里云跑（直连 egress）。

## 测试
`tests/test_fotmob.py`：7 项，纯解析器 + 真实响应样例
（`tests/fixtures/fm_*.json`），零网络。
