# 足球数据源调研报告 (第三批)

> 调研时间: 2026-10-05
> 方法: 全部结论来自本机实测 (代理CA: /run/hatch/egress-tls/ca-bundle.pem)，未实测的不写
> 状态: 4 个源已全部接入默认管线 (scripts/daily_fetch.py 第 7–10 节)

---

## 一、实测可用 ✅（已接入默认管线）

### 1. Matchbook Exchange API ⭐ 交易量数据源

- **地址**: `GET https://api.matchbook.com/edge/rest/events?sport-ids=15&per-page=200`
- **费用**: 完全免费，无需 key
- **返回**: JSON，event 含 `name`（"Home vs Away"）、`start`（ISO）、`volume`（交易量）、`markets`、`status`
- **实测** (2026-10-05): 130 场未开赛 open 场次，交易量中位数 348
- **用途**: 用户明确要求的交易量信号。`volume_weight()` 影子特征已实现
  （相对当日中位数的对数权重，上限 3.0 下限 0.2），**只记录不进生产**
- **验证**: 影子模式 → walk-forward 有效才考虑接入；验证桩 `scripts/validate_volume_shadow.py`
- **模块**: `collector/sources/matchbook.py::get_upcoming_volumes()`
- **落盘**: `data/daily/YYYY-MM-DD/matchbook_volume.json`
  （每场含 home/away/start/volume/volume_weight）

### 1b. Smarkets Exchange API ⭐ 第二交易所赔率源（2026-10-05 接入）

- **地址**: `GET https://api.smarkets.com/v3/events/?type=football_match&state=upcoming&limit=20&sort=start_datetime,id`
- **费用**: 完全免费，无需 key、无需注册
- **链路**: events → `/v3/events/{id}/markets/` 找 `market_type.name=="WINNER_3_WAY"`（全场胜平负）
  → `/v3/markets/{mid}/contracts/`（contract_type: HOME/DRAW/AWAY）
  → `/v3/markets/{mid}/quotes/`（按 contract_id 分组的 bids/offers，price 为万分比概率）
- **实测**: 买卖中点转十进制赔率（如 Barcelona vs Getafe 主胜中点 1.10）；今日 7 场竞彩（塞浦路斯/黑山等）在列
- **注意**: v3 REST **无 volume/成交额字段**——这是赔率源不是成交量源（成交量只有 Matchbook）
  limit>30 返回空，用 20+分页；sort 只能用 `start_datetime,id`
- **用途**: 1X2 买卖中点（mid_1/mid_x/mid_2）+ 买卖价差，独立交易所市场信号
- **模块**: `collector/sources/smarkets.py::get_upcoming_quotes()`
- **落盘**: `data/daily/YYYY-MM-DD/smarkets_quotes.json`（daily_fetch 第 7a 节）

### 2. Transfermarkt ⭐ 伤停 + 俱乐部身价

- **地址**: `https://www.transfermarkt.com/premier-league/verletztespieler/wettbewerbsauswahl/...`
  （伤停）；`https://www.transfermarkt.com/premier-league/startseite/...`（身价）
- **费用**: 完全免费，无需 key，静态 HTML 可抓
- **注意**: 必须带桌面 UA，否则返回极简错误页；请求间隔 ≥2 秒保持礼貌
- **实测** (2026-10-05): 伤停 68 人（含球员/位置/俱乐部/伤情/预计回归）；
  俱乐部身价 20 队（如曼城 €1430m、阿森纳 €1330m）
- **GitHub 评估** (按长期规矩先搜): `omkarcloud/transfermarkt-scraper` 仅 1★
  且为 FastAPI 服务形态需独立部署，与本项目采集器直调形态不合 → 直接静态解析
- **频率**: 伤停每日；身价每周一（低频，球队实力先验）
- **模块**: `collector/sources/transfermarkt.py::get_injuries()/get_club_values()`
- **落盘**: `data/daily/YYYY-MM-DD/tm_injuries.json`、`tm_club_values.json`（周一）

### 3. BetExplorer ⭐ 当前赔率 + 降赔榜

- **地址**: `https://www.betexplorer.com/football/<country>/<league>/`
  （注意路径是 `/football/` 不是 `/soccer/`）；降赔榜 `/football/dropping-odds/`
- **费用**: 完全免费，无需 key，静态 HTML 可抓
- **实测** (2026-10-05): 联赛页 10 行（比分/当前 1X2 赔率，`data-odd` 属性）；
  降赔榜 6–7 场（含 drop% 与庄家覆盖数）
- **注意**: 单场详情页赔率对比表是 JS 渲染，静态抓不到；
  开盘赔率已有 Titan007（90家欧指初盘），这里只取当前赔率 + 异动
- **用途**: drift 信号（当前赔率 vs Titan007 初盘对照）+ 降赔榜异动
- **模块**: `collector/sources/betexplorer.py::get_league_odds()/get_dropping_odds()`
- **落盘**: `data/daily/YYYY-MM-DD/be_league_odds.json`、`be_dropping_odds.json`

### 4. FootyStats ⭐ 球队 xG

- **地址**: `https://footystats.org/<country>/<league>/xg`
- **费用**: 完全免费，无需 key，静态 HTML 可抓
- **实测** (2026-10-05): 英超 20 队，MP/xG/xGA/xGD/GF/GA
  （如曼联 xG 2.31/xGA 1.25，布莱顿 xG 2.04/xGA 1.23）
- **注意**: 页面部分 `<td>` 未闭合 + 嵌套表格 hover-modal，HTMLParser 需防御性解析；
  队名在单元格内重复出现，需去重清洗
- **用途**: 独立于自有引擎的第三方 xG 信号，用于总进球/上下单双玩法的独立校验
- **模块**: `collector/sources/footystats.py::get_team_xg()`
- **落盘**: `data/daily/YYYY-MM-DD/fs_team_xg.json`

---

## 二、配套改动 (2026-10-05)

- **队名映射**: 新建 `collector/sources/team_names.py`
  （normalize: 小写/去 FC 后缀/&→and + lookup 查 `data/team_id_map.json`）；
  `team_id_map.json` 从 50 队补到 66 队（补 16 支英超缺失队，ID 未知留空不编造）
- **管线**: `scripts/daily_fetch.py` 新增第 7–10 节，`_summary.json` 同步更新
- **纪律**: 4 源全部只读免费，不碰 API-Football / The Odds API 配额；
  engine/ 权重/融合/门控一律未动

---

# 足球数据源调研报告 (第二批)

> 调研时间: 2026-10-01
> 方法: 全部结论来自 curl 实测 (代理CA: /run/hatch/egress-tls/ca-bundle.pem)，未实测的不写
> 背景: 已有6个数据源 (football-charts / theopenmodel / Understat / API-Football / football-data.org / The Odds API)

---

## 一、实测可用 ✅

### 1. ESPN 隐藏 API ⭐ 最高优先级

- **地址**: `https://site.api.espn.com/apis/site/v2/sports/soccer/{联赛代码}/scoreboard`
- **费用**: 完全免费，无需 key，无速率限制 (实测连续调用未被限)
- **注意**: 需加 `--compressed` (gzip)，否则 K 联赛等返回乱码
- **端点**: `/scoreboard` (赛程/比分), `/standings` (积分榜), `/teams` (球队)

**实测覆盖联赛** (2026-10-01 全部验证通过):

| 类别 | 联赛代码 | 联赛名 |
|---|---|---|
| 五大联赛 | eng.1 / esp.1 / ita.1 / ger.1 / fra.1 | 英超/西甲/意甲/德甲/法甲 ✅ |
| 次级联赛 | eng.2 / ger.2 / fra.2 / ita.2 | 英冠/德乙/法乙/意乙 ✅ |
| 荷葡苏 | ned.1 / por.1 / sco.1 | 荷甲/葡超/苏超 ✅ |
| 美洲 | usa.1 / mex.1 / bra.1 / chi.1 | 美职/墨联/巴西甲/智利甲 ✅ |
| 亚洲 | jpn.1 | J1联赛 ✅ |
| 北欧 | swe.1 / nor.1 / den.1 / fin.1 | 瑞典/挪威/丹麦/芬兰 ✅ |
| 其他 | aus.1 | 澳超 ✅ |

**不覆盖** (实测 400/FAIL):
- `kor.1` / `kor.2` — 韩K1/K2 不在 ESPN
- `jpn.2` / `jpn.3` — J2/J3 不在 ESPN

**接入难度**: 极低。纯 JSON，无认证，直接 curl 即可。
**推荐用途**: 实时比分、赛程、积分榜的主力备用源 (API-Football 100次/天用完后的补充)。

---

### 2. football-data.co.uk CSV ⭐ 高优先级

- **地址**: `https://www.football-data.co.uk/mmz4281/{赛季}/{联赛代码}.csv`
- **费用**: 完全免费，无需 key
- **注意**: 必须跟随重定向 (`-L`)，www.football-data.co.uk → football-data.co.uk，否则返回空

**实测覆盖** (2627赛季全部验证，返回字节数 > 17KB):

| 代码 | 联赛 | 代码 | 联赛 |
|---|---|---|---|
| E0–E3 | 英超–英乙 | SP1–SP2 | 西甲–西乙 |
| D1–D2 | 德甲–德乙 | I1–I2 | 意甲–意乙 |
| F1–F2 | 法甲–法乙 | N1 | 荷甲 |
| P1 | 葡超 | G1 | 希腊超 |
| T1 | 土超 | B1 | 比甲 |

**数据列** (实测 E0.csv 表头，120列):
- 比分: FTHG/FTAG/HTHG/HTAG/FTR/HTR
- 射门/犯规/角球/牌: HS/AS/HST/AST/HF/AF/HC/AC/HY/AY/HR/AR
- **赔率**: B365/BF/Pinnacle/WilliamHill等多家初盘+即时，胜平负+大小球+亚盘
- ⚠️ **勘误 (2026-10-01)**: 实测无 HxG/AxG 列，xG 不可用。欧洲 xG 仍依赖 Understat (5联赛)。

**不覆盖**: J联赛/K联赛/巴西/美职 (纯欧洲)
**接入难度**: 极低。CSV 直接下载，pandas 一行读入。
**推荐用途**: 欧洲联赛历史回填 (比分+多家赔率+射门/角球/牌三合一)，与现有 football-charts 互补。不含 xG。

---

### 3. thesportsdb (免费档)

- **地址**: `https://www.thesportsdb.com/api/v1/json/3/...`
- **费用**: 免费，公共 key = `3`
- **实测**: `search_all_teams.php` ✅, `eventsnext.php?id=133604` ✅ (返回 Arsenal 未来赛程)

**特点**:
- 球队元数据丰富 (idTeam, idESPN, idAPIfootball — 可做跨源 ID 映射！)
- 有赛程、比分、球员信息
- 无赔率、无 xG，数据深度浅
- 免费档有速率限制 (约30次/分钟)

**接入难度**: 低。
**推荐用途**: 球队 ID 交叉映射表 (ESPN ↔ API-Football ↔ thesportsdb)，赛程补充。

---

### 4. openfootball/football.json (公共领域)

- **地址**: `https://raw.githubusercontent.com/openfootball/football.json/master/{赛季}/{联赛}.json`
- **费用**: 完全免费，公共领域 (Public Domain)
- **实测**: `2026-27/en.1.json` ✅ (英超 2026/27，有比分)

**组织仓库** (实测 api.github.com/orgs/openfootball/repos):
england, espana, italy, deutschland, austria, south-america, europe, world, leagues 等

**注意**:
- 无日本/韩国专门仓库 (搜 japan/korea/brazil 无结果)
- south-america/2026/br.1.json 实测 404
- 历史数据为主，实时更新慢

**接入难度**: 低。
**推荐用途**: 欧洲历史数据补充 (公共领域，无版权顾虑)。

---

## 二、实测不可用 ❌

| 数据源 | 实测结果 | 结论 |
|---|---|---|
| SofaScore 直接 API | `{"error":{"code":403,"reason":"challenge"}}` (Cloudflare) | **不推荐** — 硬墙，需浏览器过验证 |
| FotMob 直接 API | 返回 HTML 页面 (端点失效或被拦) | **不推荐** |
| Flashscore 直接 feed | `401 Unauthorized` | **不推荐** — 已加鉴权 |
| ClubElo CSV API | 空响应 | **不推荐** — 已转认证 (与2026-09资料一致) |
| American Soccer Analysis | API 端点难找 (`/api/v1/__api__` 404) | **暂不推荐** — MLS 已有 ESPN 覆盖 |

---

## 三、GitHub 项目评估 (按用户长期规矩)

### 候选1: omkarcloud/flashscore-scraper
- **地址**: https://github.com/omkarcloud/flashscore-scraper
- **Stars**: 1 | **更新**: 2026-09-27 | **Issues**: 0
- **声称**: 免费、开源、无限制、本地运行 (`python run.py` → localhost:8000)
- **评估**: star 太少 (1个)，可信度待验证。Flashscore 直接 feed 已 401，其绕过手段未知。
- **建议**: 观察，不作为主力。如需 Flashscore 独家数据可试点验证。

### 候选2: withqwerty/open-football
- **地址**: https://github.com/withqwerty/open-football
- **内容**: 开放足球数据源的精选地图 ( datasets + scrapers + tools 索引 )
- **评估**: 不是数据源本身，是导航。有参考价值，已从中发现 ClubElo/ASA 等线索。
- **建议**: 作为持续发现新源的参考书签。

### 候选3: omkarcloud/sofascore-scraper
- **地址**: https://github.com/omkarcloud/sofascore-scraper
- **真相**: 实际是 omkar.cloud 托管的**付费 API** (免费档 1000次/月，需注册)
- **评估**: SofaScore 直接 API 已被 Cloudflare 墙，付费绕过不符合"免费"要求。
- **建议**: 不推荐。

### 候选4: vasthornet/espn-sports-scraper
- **地址**: https://github.com/vasthornet/espn-sports-scraper
- **内容**: Apify Actor 封装 ESPN 隐藏 API
- **评估**: 需 Apify 账号，而 ESPN 可直接免 key 调用，多一层无意义。
- **建议**: 不推荐 (直接用 ESPN)。

---

## 四、最终推荐 (按优先级排序)

| 优先级 | 数据源 | 费用 | 核心价值 | 接入成本 |
|---|---|---|---|---|
| **P0** | **ESPN 隐藏 API** | 免费免key | 25+联赛实时比分/赛程/积分榜，覆盖 J1/巴西/美职/北欧 | 极低 |
| **P1** | **football-data.co.uk** | 免费免key | 17个欧洲联赛，比分+多家赔率+射门/角球/牌三合一 CSV（无 xG） | 极低 |
| **P2** | **thesportsdb** | 免费 (key=3) | 跨源球队 ID 映射，赛程补充 | 低 |
| **P3** | **openfootball** | 免费 (公共领域) | 欧洲历史数据补充，无版权顾虑 | 低 |
| 观察 | flashscore-scraper | 声称免费 | Flashscore 独家数据 | 待验证 |

---

## 五、覆盖缺口分析 (竞彩常客)

| 联赛 | 现有6源 | 本次新增 | 缺口 |
|---|---|---|---|
| J1 | football-charts ✅ | ESPN ✅ | 无 |
| J2 | football-charts ✅ | — | 无 (ESPN无J2) |
| 韩K1/K2 | football-charts ✅ | — | 无 (ESPN无K联赛) |
| 巴西甲 | football-data.org ✅ | ESPN ✅ | 无 |
| 巴西乙 | The Odds API ✅ | — | 无 |
| 美职 | API-Football ✅ | ESPN ✅ | 无 |
| 北欧 (瑞/挪/丹/芬) | The Odds API ✅ | ESPN ✅ | 无 |
| 英冠/德乙等次级 | 部分 | ESPN + football-data.co.uk ✅ | 无 |
| 欧洲 xG | Understat (5联赛) | 仍只有 Understat（fd.co.uk 实测无 xG 列） | 未改善 |

**结论**: 本次新增后，竞彩常客覆盖基本无缺口。最大增益是 **ESPN 的免费实时比分/赛程**（覆盖 J1/巴西/美职/北欧）与 **football-data.co.uk 的历史比分+多家赔率**；欧洲 xG 仍只依赖 Understat 5 联赛。

---

## 六、建议接入顺序

1. **ESPN 模块** (`collector/sources/espn.py`): `get_scoreboard(联赛代码)` + `get_standings(联赛代码)`，作为 API-Football 额度耗尽时的自动降级源
2. **football-data.co.uk 模块** (`collector/sources/fd_co_uk.py`): CSV 下载 + 解析，提取比分/多家初盘收盘赔率/射门角球牌（无 xG 列，xG 仍走 Understat）
3. **thesportsdb 模块** (`collector/sources/thesportsdb.py`): 建跨源 ID 映射表 `team_id_map.json`
4. openfootball 按需补充历史数据 (暂不写模块，用时直接 curl)

---

## 七、2026-10-05 联赛扩覆盖（第四批接入的 4 源从 5 联赛扩到 15 联赛）

用户批准"加入"后，transfermarkt / betexplorer / footystats 从五大联赛扩展到 15 联赛：
英超、西甲、意甲、德甲、法甲、英冠、西乙、德乙、法乙、荷甲、葡超、巴西甲、美职、J1联赛、K1联赛。
（matchbook 本来就是全局事件流，无需扩。）

新增/修正的映射（实测发现）：
- transfermarkt：法乙 ("ligue-2","FR2")；J1 ("j1-league","JAP1")——注意不是 JPN1；K1 ("k-league-1","RSK1")——注意不是 KOR1
- betexplorer：法乙 france/ligue-2；J1 japan/j1-league；K1 south-korea/k-league-1；巴西甲路径冠名变为 brazil/serie-a-betano（旧 brazil/serie-a 已 301 到首页）
- footystats：西乙 spain/segunda-division；德乙 germany/2-bundesliga；法乙 france/ligue-2；J1 japan/j1-league（注意不是 j-league）；K1 south-korea/k-league-1；另修正旧映射：西甲 spain/laliga→spain/la-liga、葡超 portugal/liga-portugal→portugal/liga-nos（旧 slug 已被 CF 质询页取代）

实测结果（2026-10-05，逐联赛端到端）：
- transfermarkt：15/15 伤停（共 725 人）、15/15 身价（共 297 队）；J1 身价曾遇一次 405 偶发反爬，重试通过
- betexplorer：15/15 联赛赔率 + 降赔榜；K1 仅 1 行（赛季末场次少，正常）
- footystats：15/15（共 296 队 xG）

daily_fetch.py：新增 EXPANDED_LEAGUES（15 联赛），第 8/9/10 节循环抓取，逐联赛 try/except 隔离失败。
耗时影响：每日约 +2 分钟（transfermarkt 礼貌间隔 2s×15 联赛为主）；周一身价日约 +3.5 分钟。

## 八、2026-10-05 澳客/500彩票网补数据（用户点名要亚盘/大小球/阵容/伤停/H2H/xG）

用户指出澳客网和500彩票网有这些数据，要求抓回。实测结论：

### 澳客 (www.okooo.com) —— 部分可用 ✅（history 页已接入默认管线第 11 节）

- **`/jingcai/` 当前对阵页**：200。含 data-mid、队名、让球rq、竞彩SP。注意日期页
  `/jingcai/YYYY-MM-DD` 会 301 跳到带斜杠地址再 405（反爬），只能用当前页。
- **`/soccer/match/{mid}/history/`**：200，~300KB 纯静态。含两队交锋（H2H）、
  双方近10场战绩、联赛排名、未来赛程；每场附 99家平均欧指终指 + 365亚盘
  （水位/盘口/水位）；首行为本场即时指数。7/7 一次抓通（后又 7/7，14/14 稳定）。
- **`/soccer/match/{mid}/odds/`**：部分 200 但只是 AJAX 壳，欧赔数据走
  `Remoting/json.php`，静态抓不到。
- **`/ah/`、`/overunder/`、`/qingbao/`（情报/阵容伤停）、对阵页 base**：
  405 反爬（时好时坏，重试偶尔可过），不稳定，不依赖。
- **静态缺失**：大小球、欧赔/亚盘初盘、阵容/首发/伤停、xG（澳客无 xG）。
- **模块**：`collector/sources/okooo.py`（`get_board_map`/`find_mid`/`get_match_history`，
  405 有限重试 + 4s 礼貌间隔）；落盘 `data/daily/YYYY-MM-DD/okooo_history.json`。

### 500彩票网 —— 静态不可用 ❌（只报"手动/单次可用"，不硬接）

- `www.500.com` 首页 200：有 7 场对阵卡片，链接全部指向 `odds.500.com/fenxi/youliao-{id}.shtml`
  （7 场 ID 已映射：1398658/1398656/1398612/1398659/1398609/1398654/1398615）。
- `odds.500.com` 全站 EO_Bot_Ssid JS 质询，静态抓不到任何数据页。
- `live.500.com` 200 但数据 JS 驱动，静态无对阵 ID。
- 结论：500 的数据（亚盘/大小球/情报）只能走浏览器人工看，不做长期静态源。

### 本次 7 场实际补回的数据

`data/daily/2026-10-05/okooo_500_matches.json`：
- 每场：双方近10场战绩（含每场欧指+亚盘）、H2H（1~10场）、本场即时欧指99家平均、
  本场即时亚盘365（盘口+水位）。
- 仍缺（已如实标缺失）：大小球、欧赔/亚盘初盘、阵容/首发/伤停、xG。
  - 伤停可用 transfermarkt（俱乐部口径；国家队比赛日本身无俱乐部伤停概念，
    且开球前 14h 首发未公布，属合理缺失）。
  - 大小球/初盘：vip.titan007.com（亚让13家/大小球16家）本机仍 000 不通；
    500/澳客对应页静态不可抓 → 需浏览器单次抓取，或接受缺失。

---

## 二、theopenmodel 替代源调研（2026-10-08）

> 背景：theopenmodel（Hicruben，Elo+Dixon-Coles+Monte Carlo，五大联赛赛前三向概率CSV，CC BY 4.0）
> 已连续4天停更；主页时间冻结在2026-09-09（"Forecast saved Sep 9"），CSV可下载但最新kickoff为2026-09-16，
> 系作者弃更，非临时故障。按"先搜GitHub、三选一对比"规矩找替代。
>
> ⚠️ 诚实注（先于候选）：`openmodel_predictions.json` 经查**从未被任何下游消费**
> （只有 daily_fetch.py 自己写文件，engine/scripts/report 均未读取）。
> "第4个独立信号/分歧检测"只是文档意图，从未真正接入。因此先有决策①：是真正把替代源接入分歧检测，
> 还是承认不需要这个槽位、直接摘掉。以下对比按"决定要"的前提写。

### 候选1：API-Football /predictions ⭐（推荐：直接用）

- **地址**: `GET https://v3.football.api-sports.io/predictions?fixture={id}`（key 已在 `.af_key`，无需新申请）
- **实测**（2026-10-08，本机代理）：200，结构化JSON；`predictions.percent={home,draw,away}` 直接给三向概率
  （实测 Cruzeiro vs Sao Paulo：`{home:10%, draw:45%, away:45%}`），另有 winner/advice/goals 字段
- **覆盖**：API-Football全联赛（远超 theopenmodel 的五大联赛）
- **更新频率**：赛前持续更新（商业服务）
- **费用/配额**：免费档 100次/天（实测 header：`x-ratelimit-requests-limit: 100`）；每次预测=1次调用，
  20–40场/天的批次 ≈ 20–40% 配额，**与现有 fixtures 抓取共享同一配额池**
- **稳定性**：api-sports.io 商业服务，持续维护；本机代理链路通
- **独立性**：第三方组织、方法学不公开；是"组织独立"非"方法独立"（与咱们Elo+Dixon-Coles可能同宗）
- **接入成本**：低——复用现有 `collector/sources/apifootball.py` 的 key 与 client 模式
- **最简接入方案**（不写代码，等拍板）：
  1. `collector/sources/` 新增 `apifootball_predictions.py`（或并入 apifootball.py）：
     `get_predictions(fixture_id) -> {p_home, p_draw, p_away, advice}`
  2. `daily_fetch.py` 新增一节：对当日重点场次批量拉取，
     存 `data/daily/YYYY-MM-DD/afb_predictions.json`，沿用 openmodel 的 schema
    （kickoff/league/home/away/p_home/p_draw/p_away/source），保证下游无缝替换
  3. 配额守卫：每日上限40次，超限跳过并记录；fixtures 抓取优先
  4. 下游（此前openmodel没做到的）：真正接入分歧检测——与引擎v2.6三向概率对照，
     首选方向不一致且 gap≥0.15 标独立信号分歧（沿用B补丁门控口径）

### 候选2：自建轻量Elo第二意见（备选：自己搭）

- **做法**：用已在管线的 football-data.co.uk 17联赛历史比分，跑独立轻量Elo
  （固定 K=20、HFA=100，不做市场融合），Elo差 → 标准logistic转1X2；
  可再叠一层 Understat xG Poisson（xG数据已有，每周任务在抓）做方法学对冲
- **费用**：零配额、零key、永不死
- **覆盖**：17个欧洲联赛（小于API-Football，但覆盖咱们15联赛管线的主体）
- **更新频率**：随 daily_fetch 每天更新
- **稳定性**：自己拥有，~80行代码，无外部依赖
- **独立性**：⚠️ 部分——同数据源 lineage（football-data.co.uk），但方法与主引擎
  （Dixon-Coles+市场融合+校准层）不同；"独立性"更多是心理安慰，实质是第二套参数
- **接入成本**：中——要写Elo模块+回测验证（按纪律需walk-forward证明有增量才进生产）
- **结论**：可做，但优先级低于候选1；适合作为"无配额压力时的影子信号"

### 候选3：已排除项（实测否决）

| 候选 | 否决原因（本机实测） |
|---|---|
| ClubElo API | `/Fixtures`（预计算胜平负概率）返回 "Fixtures API deactivated"；其余端点 502/空响应；仅主站存活。API实质已死 |
| Forebet | 本机 `403`（反爬）；无公开API；违反"稳定、不墙机房IP"规则 |
| Polymarket Gamma API | `/events` 可达、免key，但本质是第4个**市场**源（已有Odds API/Matchbook/Smarkets三个），非独立模型，边际价值低 |
| ESPN predictor | 足球scoreboard无predictor字段（仅美式足球有），此路不通 |
| GitHub每日预测CSV项目 | 搜到的多为世界杯专项或自建dashboard（如 amozaffari/PL2026 仅英超），无 theopenmodel 式的多联赛每日CSV发布者 |

### 对比总表

| 维度 | API-Football predictions | 自建轻量Elo | Forebet/ClubElo |
|---|---|---|---|
| 第三方模型独立性 | ✅ | ⚠️（同源） | ❌（已死/被墙） |
| 三向概率直接可用 | ✅ percent字段 | 需自己转 | — |
| 联赛覆盖 | 全（>五大联赛） | 17欧洲联赛 | — |
| 更新频率 | 赛前持续 | 每天 | — |
| 接入成本 | 低 | 中（需回测） | — |
| 费用/配额 | 100/天共享 | 零 | — |
| 本机实测 | ✅ 200 | —（自建） | ❌ |

### 建议

**直接用 API-Football /predictions**（候选1），理由：
1. 实测下来它是**唯一存活、免新申请、结构化**的第三方模型三向概率源
   （theopenmodel死、ClubElo API死、Forebet墙、GitHub无同类发布者）
2. Key已有、client模式可复用，接入成本最低；覆盖反超 theopenmodel 的五大联赛
3. 唯一成本是配额：与fixtures共享100/天。缓解：只对重点场次调用（建议日上限40次），
   fixtures优先；配额用满则降级跳过

但先请用户拍板决策①：**替代源是否真正接入分歧检测**，还是直接摘掉 theopenmodel 槽位。
theopenmodel 的文件4天来写了没人读——如果只是"补上一个没人用的槽位"，不如摘掉省配额。
