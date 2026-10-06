# 北单低级别联赛数据源调研（2026-10-06 实测）

目标：补北单开售赛程中的低级别联赛短板（J2、K2、挪威甲/乙、瑞典甲、巴西乙、英甲/英乙、苏超/苏冠等）。
已有管线（不再重复推荐）：football-data.co.uk、ESPN 隐藏 API、theopenmodel、ApiFootball、football-data.org、
The Odds API、Titan007、7M体育、Smarkets、Matchbook、footystats、transfermarkt、betexplorer、BrazilianFootball/Data。

实测环境：本机经 MITM 代理出站，`curl --cacert /run/hatch/egress-tls/ca-bundle.pem`，
UA 为常规 Chrome。GitHub 维护状态经 api.github.com 核验。

---

## ① 推荐长期接入

### 1. FotMob 非官方 JSON API（`www.fotmob.com/api/data/...`）——最高优先级
- **实测**：全部 200。
  - `/api/data/matches?date=20261006` → 200，59 个联赛，含 `Série B`、`3. Divisjon Avd. 5`（挪威第4级）、`Isthmian Premier Division` 等深层低级别。
  - `/api/data/allLeagues?ccode3=JPN` → 200；确认联赛 ID：J2=8974、J3=9136、K2=9116、K3=9537、OBOS-ligaen=203、2.divisjon=204、Superettan=168、Ettan=169、巴西 Serie B=8814、Serie C=8971。
  - `/api/data/leagues?id=8974&ccode3=JPN` → 200，693KB，含 `table`（20 队完整积分榜：played/wins/draws/losses/scoresStr/pts）、`teamForm`、`fixtures`（allMatches/firstUnplayedMatch）、`overview`、`stats`。
  - `/api/data/teams?id=162192`（Vegalta Sendai）→ 200，271KB，含 `fixtures`、`squad`、`history`、`stats`。
  - `/api/data/match?id=5144585` → 200，单场详情（含 `odds: null`——**无赔率数据**）。
- **数据类型**：赛程、比分、积分榜、球队近况/阵容/历史赛季赛程。**无赔率、无亚盘、无伤停。**
- **免费额度**：无 key、无登录，当前无可见限流（仍建议自限速）。
- **抓取难度**：低。纯 JSON，`requests` 即可。
- **注意**：非官方接口，随时可能变更；此前 2026-10-01 曾被判不可达，本次实测已恢复——接入时加失败告警。
- **定位**：低级别联赛**赛程/比分/积分榜/球队近况**的主力新源，直接进默认管线。

### 2. jordantete/OddsHarvester（OddsPortal 历史赔率抓取，MIT，PyPI `oddsharvester`）
- **维护状态（实测）**：2026-10-05 仍在提交（昨天），256 stars，7 open issues，有 `scraper_health_check.yml` 定时健康检查 CI。
- **覆盖（读其联赛常量表实测确认）**：`brazil-serie-b`、`japan-j2-j3-league`、`south-korea-k-league-2`、`norway-obos-ligaen`、`sweden-superettan`——正好命中缺口。
- **玩法**：`1x2`、`asian_handicap`（umbrella 自动展开全部盘口线，±5 到 quarter 档共 25 档）、`over_under`（umbrella 展开全部大小球线）、`btts`、`dnb`、`european_handicap`。支持 `upcoming`（未来赛程）与 `historic`（按赛季回填）两种模式，输出 JSON/CSV。
- **实测**：`www.oddsportal.com` 根域名 curl 200 可达。
- **免费额度**：无 key，抓公开页。
- **抓取难度**：中。基于 Playwright 浏览器自动化，较重；适合**批量历史回填**（低级别亚盘/大小球历史是目前唯一找到的免费批量方案），不适合高频 daily。OddsPortal 有 Cloudflare，Playwright 实际通过率需集成时验证。
- **定位**：低级别**历史亚盘/大小球/1x2 赔率回填**专用（每周或按需批量），不进每日实时链。

---

## ② 备选

| 候选 | 实测 | 覆盖/数据类型 | 说明 |
|---|---|---|---|
| soccerway.com | 307→`www.soccerway.com` 200；J2 2025 页 637KB 全量 HTML | H2H、战绩、积分榜（J2 等全覆盖） | 静态 HTML 好抓；与 Titan007/7M 的 H2H 部分重叠，定位低级别 H2H 补充 |
| schochastics/football-data | GitHub 可直下（parquet） | 1.2M 场/207 联赛/1888–2023，ODC-BY | **仅顶级联赛**，无 J2/K2/挪甲/瑞甲/巴乙；对 J1/K1/Eliteserien/Allsvenskan/巴甲 的 Elo burn-in 有用 |
| data.j-league.or.jp（J.League Data Site） | 302→`/SFTP01/` 200，124KB | J1/J2/J3 官方赛程赛果 | 日文 HTML 表格；J2 官方交叉验证源 |
| nowgoal.com | 200，104KB | 低级别+赔率+H2H，中文界面 | 与 betexplorer/Titan007 部分重叠 |
| kleague.com | 302→`/index.do` 200 | K 联赛官方（韩文） | 数据入口需浏览器导航确认 |
| svenskfotboll.se | 200，225KB | 瑞典足协官方 | 备选 |
| injuriesandsuspensions.com | 200，189KB | 100+ 联赛伤停/停赛（含低级别） | 免费版仅部分场次，详细 BEST INFO 付费；低级别伤停备选（主源仍是 transfermarkt） |
| soccerpunter.com | 根域名 200 | H2H/积分榜/赔率 | 联赛 URL 结构待核实（猜测的 standings 路径 404），勿猜 URL |
| oseymour/ScraperFC | 414 stars，2026-05-13 更新，v4.5.0（2026-04） | 统一抓取：FBref/SofaScore/Transfermarkt/Understat/ClubElo/ESPN | 但 FBref 403、SofaScore API 403、ClubElo 不通，其多数模块被墙，现阶段增量有限；墙解除后重评估 |
| withqwerty/open-football | 2026-10-03 仍在更新，34 stars | awesome 清单（非数据源） | 选型时备查 |
| openfootball/world | GitHub tree 实测：日本仅 `2019–2025_jp1.txt`，无 J2、无韩国 | CC0 | J1 补充，无低级别 |
| murdy46/football-data-db | 2026-10-06 当天推送，1 star | SQLite，21 联赛（英格兰到第5级、苏格兰到第4级） | 太新太小，先观察 |

---

## ③ 放弃

| 候选 | 实测证据 | 原因 |
|---|---|---|
| worldfootball.net | 403 | 被墙 |
| api.clubelo.com | http 403 / https 空响应（502） | 不通（与 10-01 结论一致） |
| SofaScore API（`www.sofascore.com/api/v1/...`） | 403 | 仍被墙 |
| whoscored.com | 403 | 被墙 |
| fbref.com | 403 | 被墙（需 headed 浏览器，放弃直抓） |
| forebet.com | 403 | 被墙 |
| soccerstats.com | 403 | 被墙 |
| playmakerstats.com | 403（Cloudflare "Just a moment..."） | 强反爬 |
| fotball.no（挪威足协） | 连接失败（000） | 不可达 |
| footballdatabase.eu | 连接失败（000） | 不可达 |
| defnlnotme/football-apis | 2025-07-01 后无更新，0 star | 废弃 |
| omkarcloud/flashscore-scraper | 1 star 新项目；商业版付费（1000次/月免费） | flashscore.com 虽 200 可达但 feed 需逆向；已有 FotMob 覆盖同类需求 |
| statarea.com | 200 但纯预测站 | 数据价值低 |

---

## 缺口诚实说明
1. **低级别伤停**：除 transfermarkt（已有）和 injuriesandsuspensions（部分免费）外，无强免费源。J2/K2 建议接受 transfermarkt 为主，不硬找。
2. **低级别 xG**：footystats 已有 296 队；FBref 被墙；本次无新增。
3. **低级别亚盘/大小球历史**：OddsHarvester + OddsPortal 是唯一找到的免费批量方案（代价是 Playwright）。
4. FotMob 单场详情 `odds` 字段为 null——**赔率仍需靠 The Odds API / Titan007 / OddsHarvester**，FotMob 只补赛程比分积分榜。
5. 本次另有并行结论（北单赛程/SP 源：澳客 okooo.com BJBet 页面、500 trade.500.com/bjdc、澳客 danchang/kaijiang 赛果页）已记入 MEMORY.md，与本报告互补：本报告管的是**国际低级别联赛基础数据**，那份管的是**北单官方在售/SP/赛果**。
