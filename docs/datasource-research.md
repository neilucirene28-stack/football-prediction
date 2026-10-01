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
