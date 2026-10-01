# 数据源字段审计表

> 审计日期：2026-10-01。依据：各模块源码 + 2026-10-01 当天的真实抓取记录。
> 凡标注"已验证"的是当天真实跑通过的；"未验证"的是只读过代码、没跑过真机/真接口的，不编造。

## 通用说明

- 8 个新模块的网络层已统一走 `collector/sources/_http.py`：
  `fetch_with_retry`（3 次重试、指数退避 1s/2s/4s；429 等 60s 再试；
  4xx 除 408/429 外直接抛 `HTTPError` 不重试）、`download_with_retry`、
  `clean_no_proxy`（import 时自动清理 `no_proxy` 里的 `[::1]`）。
- 约定：解析层缺字段时填 `""` / `None` / `[]` / `{}`，不抛异常；
  网络层/协议层错误（4xx、超时耗尽、API errors）向上抛异常，由调用方决定。

---

## 1. API-Football（api-sports.io，key 在 `.af_key`）

| 函数 | 关键字段 | 字段含义 | 缺失时填什么 | 真机验证 |
|---|---|---|---|---|
| `get_fixtures` | fixture_id / home / away / home_id / away_id | 比赛 ID、主客队名、球队 ID | 队名 `""`，ID `None` | ✅ 已验证（2026-10-01：今日 125 场/明日 305 场） |
| | date | 开球时间（ISO，UTC） | `""` | ✅ 同上 |
| | status | NS=未开赛、FT=完赛、1H/2H=进行中 | `""` | ✅ 同上 |
| | goals_home / goals_away | 比分 | 未开赛填 `None`（注意：是 None 不是 0） | ✅ 同上 |
| | league | 联赛名（英文） | `""` | ✅ 同上 |
| `get_status` | account / requests_used / requests_limit | 账户名、今日已用/上限（100/天） | `""` / 0 / 100 | ✅ 已验证（账户名"问天"） |
| `get_standings` | rank / team / played / win / draw / lose / gf / ga / points | 积分榜 | 数字缺失填 0 | ⚠️ 未验证（只跑过单元测试用的合成载荷） |
| `get_live_scores` | 同上 + elapsed（已进行分钟） | 实时比分 | 同上 | ⚠️ 未验证 |

- API 返回体里 `errors` 非空 → 抛 `RuntimeError("API 错误: ...")`（如额度用尽、参数错）。
- 备注：球队名是英文（如 "Arsenal"），与小店火中文名对接需要映射表（目前没有）。

## 2. football-data.org（key 在 `.fd_key`）

| 函数 | 关键字段 | 字段含义 | 缺失时填什么 | 真机验证 |
|---|---|---|---|---|
| `get_matches` | match_id / home / away | 比赛 ID、主客队（英文全称） | 队名 `""` | ✅ 已验证（2026-10-01：13 项赛事可用） |
| | utc_date | 开球时间（UTC ISO） | `""` | ✅ 同上 |
| | status | TIMED/SCHEDULED/LIVE/FINISHED 等 | `""` | ✅ 同上 |
| | score_home / score_away | 全场比分 | 未开赛或 `fullTime: null` 时填 `None` | ✅ 同上 |
| | competition / competition_code | 联赛名/代码（PL、PD…） | `""` | ✅ 同上 |
| `get_standings` | position / team / played / won / draw / lost / gf / ga / points | 积分榜（只取 type=TOTAL 的表） | 数字缺失填 0 | ⚠️ 未验证（`__main__` 烟雾跑过，但 10-01 当天抓取记录里没用它） |

- 免费档 10 次/分钟；超限/4xx 由 `_http` 抛 `HTTPError`，`get_matches` 不吞异常。
- 注意：不覆盖 J2、韩K1/K2、中超。

## 3. The Odds API（key 在 `.odds_key`）

| 函数 | 关键字段 | 字段含义 | 缺失时填什么 | 真机验证 |
|---|---|---|---|---|
| `get_odds` | match_id / home / away / commence_time | 比赛 ID、主客队、开球时间 | `""` | ✅ 已验证（2026-10-01：五大联赛 96 场，本月已用 16/剩 484） |
| | bookmakers | {博彩公司名: {市场名: {选项名: 赔率}}} | 无公司填 `{}` | ✅ 同上 |
| | h2h 市场 | {主队名: 主胜, "Draw": 平, 客队名: 客胜} | 缺 price 填 `None` | ✅ 同上（只抓了 h2h；spreads/totals 未验证） |
| `get_quota` | used / remaining | 本月已用/剩余额度（从响应头 `x-requests-used/remaining` 读） | 读不到填 `None` | ✅ 已验证 |

- 计费按"有赔率的比赛场次"扣，不是按调用次数；免费 500 次/月。
- ⚠️ key 是拼在 URL query（`apiKey=...`）里的，会进代理/服务器日志。额度敏感，建议后续改走 header（但官方只支持 query，这是 API 设计问题，改不了，只能注意日志脱敏）。
- 赔率是快照值，没有"初盘/收盘"时间序列；漂移分析需要自己定时快照。

## 4. ESPN 隐藏 API（免 key）

| 函数 | 关键字段 | 字段含义 | 缺失时填什么 | 真机验证 |
|---|---|---|---|---|
| `get_scoreboard` | event_id / home / away / home_id / away_id | 比赛 ID、主客队（英文显示名）、ESPN 球队 ID | 队名 `""` | ✅ 已验证（2026-10-01：英超/J1/巴西甲/美职冒烟通过，16 场） |
| | date | 开球时间（UTC ISO） | `""` | ✅ 同上 |
| | status | `shortDetail`，如 "FT"、"10/4 - 1:00 PM EDT"（未开赛是文字时间，不是标准状态码） | `""` | ✅ 同上 |
| | score_home / score_away | 比分（**字符串**，如 "2"；未开赛是 `""`） | `""` | ✅ 同上 |
| | league | 联赛英文名 | `""` | ✅ 同上 |
| `get_standings` | team / played / won / draw / lost / gf / ga / points | 积分榜 | 数字缺失填 0 | ⚠️ 未验证 |

- ⚠️ **发现 bug（已记录未修）**：`get_standings` 里 `"rank"` 字段取的是 `team.id`（球队 ID），不是真实排名。`team_id` 也是球队 ID，两者重复。用之前必须修。
- ⚠️ `score_home` 是字符串 `""` 而不是 `None`，与其他源的 `None` 口径不一致，下游做数值运算前要先清洗。
- 隐藏 API，无官方文档；字段/端点可能随时变（`LEAGUE_CODES` 里 kor.1/jpn.2 已实测 400 不可用）。不能作为唯一生产依赖，已在每日脚本里定位为"API-Football 备用源"。
- 必须带 `--compressed`（gzip），否则部分联赛返回乱码——已在 `_request` 里处理。

## 5. football-data.co.uk（免 key，CSV）

| 函数 | 关键字段 | 字段含义 | 缺失时填什么 | 真机验证 |
|---|---|---|---|---|
| `download_csv` | league_code / season | 如 E0/2627；**必须跟随重定向**（`-L`） | 下载失败抛异常；文件 <1000 字节抛异常 | ✅ 已验证（红黄牌项目用过 4 赛季×5 联赛 7156 场） |
| `parse_csv` | date / home / away | 日期（`03/10/2026` 英式）、主客队（英文） | `""` | ✅ 同上 |
| | score_home / score_away（FTHG/FTAG）| 全场比分 | 非法/空填 `None` | ✅ 同上 |
| | score_ht_home / score_ht_away（HTHG/HTAG）| 半场比分 | 同上 | ✅ 同上 |
| | result（FTR）| H/D/A | `""` | ✅ 同上 |
| | shots_home/away（HS/AS）、corners（HC/AC）| 射门/角球 | 同上 | ✅ 同上 |
| | odds_home/draw/away（B365H/D/A）| Bet365 **初盘**胜平负 | 同上 | ✅ 同上 |
| | odds_close_*（PSCH/PSCD/PSCA）| Pinnacle **收盘**胜平负 | 同上 | ✅ 同上 |

- ⚠️ **重要勘误**：`docs/datasource-research.md` 第四、五节仍写着"17 个欧洲联赛免费 xG"，但 2026-10-01 实测 120 列表头**没有 HxG/AxG 列**。本模块 docstring 和解析器已按"无 xG"处理（`parse_csv` 结果里没有 xG 字段，测试已锁定），研究报告的旧说法需要改掉，不要再引用。
- 覆盖 17 个欧洲联赛；J2/韩K/中超没有。
- 赛季格式是 `2627` 这种 4 位串，注意不要传成 `2026-27`。

## 6. thesportsdb（免费公共 key=3）

| 函数 | 关键字段 | 字段含义 | 缺失时填什么 | 真机验证 |
|---|---|---|---|---|
| `search_teams` | name / thesportsdb_id / espn_id / apifootball_id / country | 球队名、各源 ID | `""` | ✅ 已验证（已生成 `data/team_id_map.json`，50 队） |
| `build_id_map` | 同上，key 为小写队名 | 跨源 ID 映射表 | 某联赛失败打印告警跳过 | ✅ 已验证（五大联赛） |
| `lookup` | team_name（大小写不敏感） | 查映射表 | 无缓存文件/查不到返回 `None` | ✅ 已验证 |
| `get_next_events` | event_id / date / home / away / league | 球队未来赛程 | — | ⚠️ 未验证 |

- ⚠️ 免费接口每个联赛只返回 10 队，当前 50 队不是完整五大联赛名单；且英超有 20 队，只拿到 10 队。映射表需要补全/换方式验证。
- `build_id_map` 以小写队名为 key，不同联赛同名球队会互相覆盖（罕见但存在）。

## 7. football-charts（key 在 `.fc_key`，免费 5000 次/天）

| 函数 | 关键字段 | 字段含义 | 缺失时填什么 | 真机验证 |
|---|---|---|---|---|
| `get_results` | matches[]: date / homeTeam / awayTeam / score("2:1") / ht_result | 赛果 | — | ✅ 已验证（J2/K1/K2 回填 1627 场） |
| `results_to_history` | date / home / away / hg / ag / hthg / htag / source | 内部历史格式 | 非法比分（如"延期"）整行跳过；半场解析失败填 `None` | ✅ 同上 |
| `fetch_league_history` | seasons 列表 | 多赛季合并去重（按 date+home+away） | 某赛季抛错→打印告警并跳过该赛季，返回其余 | ✅ 同上 |
| `parse_held_seasons` / `is_unknown_season_error` | 从错误文本解析 "Seasons held: 2025, 2024..." | 赛季不存在时自动回退用 | 无匹配返回 `[]` / `False` | ⚠️ 未验证（纯函数，只有单元测试） |
| `get_leagues` | 联赛列表 | — | — | ✅ 烟雾跑过 |
| `get_fixtures` / `get_table` | 赛程 / 积分榜 | — | — | ⚠️ 未验证 |

- 免费 tier 只覆盖当前+上赛季；J2 的 2026 赛季不存在会报 `unknown_season`（HTTP 400，`_http` 直接抛 `HTTPError` 不重试）。
- ⚠️ **每日脚本的问题**：`daily_fetch.py` 对 J2 传了 `seasons=["2026"]`，`fetch_league_history` 内部吞掉异常返回 `[]`，汇总却记为"源成功"。语义应该是"合法缺失"而不是"成功"，否则掩盖缺口。`parse_held_seasons` 已经能解析出实际持有赛季，建议每日脚本改用"先探测可用赛季再拉取"。
- 队名是英文（如 J2 的英文队名），与小店火中文名映射还没做。

## 8. theopenmodel（免 key，CC BY 4.0）

| 函数 | 关键字段 | 字段含义 | 缺失时填什么 | 真机验证 |
|---|---|---|---|---|
| `get_predictions` | kickoff（tz-aware datetime）/ league（已映射中文）/ home / away | 未来赛程预测 | — | ✅ 已验证（2026-10-01：55 条） |
| | p_home / p_draw / p_away / model_pick | 胜平负概率、模型推荐 | 概率解析失败整行跳过 | ✅ 同上 |
| | league_code | 原始联赛代码（premier-league…） | — | ✅ 同上 |
| `filter_upcoming` | predictions, asof | 只保留 kickoff > asof 的 | kickoff 为空的行丢弃 | ✅ 单元测试（纯函数） |
| `snapshot_is_stale` | predictions, asof, max_age_days=3 | 最新一场 kickoff 距 asof 超 3 天 → True；空列表 → True | — | ✅ 单元测试（纯函数） |
| `find_match` | 模糊匹配队名（去空格/横杠/点，大小写不敏感） | 找某场比赛的预测 | 找不到返回 `None` | ⚠️ 未验证（只有单元测试） |
| `get_track_record` | total / correct / accuracy | 官方历史战绩自述 | — | ⚠️ 未验证（`__main__` 跑过，10-01 记录里没用它） |

- 缓存 `beidan-mvp/data/openmodel/predictions.csv`，12 小时 TTL。
- ⚠️ **新鲜度风险（2026-10-01 已发现）**：文件里"未结算"记录最新只到 09-11，且之前代码只按 `result` 为空判断未开赛。已加 `filter_upcoming`（kickoff 过滤）接入 `get_predictions`，`snapshot_is_stale` 供调用方判断快照是否过期。但**调用方（daily_fetch）还没用 `snapshot_is_stale`**，建议接上：stale 时不要把预测当成当天的用。
- 只覆盖五大联赛；与小店火对接时只在"有对应比赛"时用（用户已确认的规则）。

---

## 审计发现的问题清单（待排期修）

1. **espn.get_standings 的 rank 字段是球队 ID**（复制粘贴 bug），用前必修。
2. **ESPN 比分是字符串 `""`**，与其他源 `None` 口径不一致，下游要清洗。
3. **daily_fetch 把 football-charts J2-2026 的 `unknown_season` 记为"源成功"**，应改为合法缺失或用 `parse_held_seasons` 自动回退到实际持有赛季。
4. **daily_fetch 没接 `snapshot_is_stale`**，theopenmodel 快照过期时无告警。
5. **研究报告（datasource-research.md 四、五节）仍声称 football-data.co.uk 有免费 xG**，与实测矛盾，需修正措辞。
6. **thesportsdb 映射表只有 50 队**（免费接口每联赛 10 队），不是完整五大联赛。
7. **The Odds API 的 key 拼在 URL query 里**，注意日志脱敏（API 本身只支持 query，改不了）。
8. **football-data.org / oddsapi 的 `get_quota`**：quota 读不到时填 `None`，调用方不要直接做数值比较。
9. 各源队名语言不统一（英文为主），与小店火中文名之间缺一张完整的映射表（J2/K1/K2、theopenmodel 都是）。
