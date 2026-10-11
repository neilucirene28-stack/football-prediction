# 竞彩第十轮：赛程核验、统计分页与追加情报

本轮在v9基础上完成数据消费链路的三个修复和时效/覆盖诊断。只属于竞彩，未部署到MUSE，未生成今日预测，没有启用新的概率系数。

## 修复的具体行为

| 原行为 | v10行为 |
|---|---|
| API阵容/伤停仅检查返回查询fixture及队伍ID | 先取得 `/fixtures?id=...` 原件，核对原生fixture、联赛、赛季、主客队、带时区开球时间及NS未开赛状态；若另有时间戳也须一致。赛程失败不继续请求数据 |
| 玩家统计多页直接拒绝，需外部自行规范化 | 新增完整分页适配和采集；逐页归档，按源内current排序，检查页码、总页数、查询球队/联赛/赛季、results条数、每页20条契约和跨页球员重复。任何缺页/截断页/变化/失败都不发布部分统计 |
| 每次导入替换team_observations | 默认保留旧包再追加新包；重复原包拒绝计数。同源多包仍需显式合并，已知字段冲突拒绝；`--replace-observations`仅在新输入中显式替换，不改旧文件 |
| 情报输出只有状态和特征 | 新增逐证据采集/可得/发布时间距快照的秒数、来源最早/最近采集年龄、可用性/注册/赛季统计字段已知人数；不猜原文更新时刻 |

API-Football官方教程说明玩家统计有分页，页大小20，并要求读取current/total取得后续页面。依据：[完整玩家教程](https://www.api-football.com/news/post/how-to-get-all-teams-and-players-from-a-league-id)、[调用与额度教程](https://www.api-football.com/news/post/how-to-optimize-api-sports-calls-and-quota-usage)、[官方接口指南](https://www.api-football.com/news/post/how-to-get-started-with-api-football-the-complete-beginners-guide)。本轮仅把NS作为可消费的赛前状态，TBD、推迟、进行中和已结束均不提升为有效赛前情报。

## 来源证据与完整性

新增 `collector/jingcai_api_football.py`。有效包必须带 `fixture_context`，其中原生赛事/赛季/比赛/主客ID、开球时间与输入绑定一致，且引用赛程原件证据。模型入口拒绝缺此上下文的有效API包；规范化导入还会读取该证据原件，重新解析上下文后比较。低层v9单页解析函数保留结构兼容，但其单独输出不再能作为有效API情报进入模型。

每个赛程和统计页各有独立URL、SHA256、实际collected_at/available_at。统计球员引用各自页面证据，不能用第一页摘要证明所有页。任一页失败，失败包保留已请求的证据并将teams置null；不输出前面已经成功的部分球员。现有不绕过安全拦截、无自动重试、禁止凭据URL和自动重定向的纪律不变。

默认单队最多10页，不包含额外1次赛程请求；可用 `--max-api-pages` 设置1至100。读取日/分钟剩余额度头，已知为0则停止；HTTP429等失败直接停止。未取得额度头不声称额度充足，也不会把停止后的数据补成0。

完整查询分页 **不等于完整的当前注册名单**。`roster_complete`保持false；只输出观测到的球员统计。`observed_season_yellow_sum/red_sum/minutes_sum`是已观测球员汇总，完整名单总量、缺阵贡献占比和未知注册身份继续保持null。查询主队时不把响应中附带的其他球队统计提升为客队情报；需另行请求客队并在同原生ID作用域合并。

`coverage`展示本来源观测玩家的availability_known_n、registration_known_n和各season_stat_known_n，不能把已知记录数量等同实际阵容覆盖率。`evidence_ages`与`freshness`的参照点是原输入snapshot_at，不是后续复盘日期；采集较新不能证明网页文字刚更新，没有擅自设置时效阈值或修改胜率。

## 接入命令

所有命令在项目根目录执行。绑定文件包含与竞彩输入精确对应的match，以及API原生数字字符串event_id、home_id、away_id、competition_id、season_id；比赛与中文球队映射仍由调用方明确核验，不能根据译名自动批准。

### 实时玩家统计

API_FOOTBALL_KEY由运行环境提供，不写入命令参数或示例文件。

```bash
python3 scripts/import_jingcai_observations.py --payload input.json \
  --collect api_football --kind api-players --binding binding.json \
  --capture-dir captures/new-run --max-api-pages 10 \
  --refresh-snapshot --output enriched-input.json
```

原件目录包含fixture/response.body及其meta、data-page-N/response.body及其meta。客队统计在binding中设置stats_team_side为away；默认home。API阵容/伤停改为api-lineups/api-injuries，同样先核赛程。

### 离线完整分页

pages.json为数组，每项如 `{"raw":"data-page-1/response.body","meta":"data-page-1/response.meta.json"}`，路径相对于清单文件目录。必须包含所有页，允许原件排列顺序不同但不能缺页或重复。

```bash
python3 scripts/import_jingcai_observations.py --payload input.json \
  --api-pages captures/new-run/pages.json --binding binding.json \
  --fixture-raw captures/new-run/fixture/response.body \
  --fixture-meta captures/new-run/fixture/response.meta.json \
  --output enriched-input.json
```

单页API原件也须提供fixture-raw/fixture-meta；multi-page不能只用 `--raw` 导入第一页。其他非API来源原件的v9命令保持适用。

### 追加来源和同源合并

```bash
python3 scripts/import_jingcai_observations.py --payload enriched-input.json \
  --packets new-packets.json --evidence-files new-evidence-files.json \
  --merge-same-source --output combined-new-input.json
```

只需提供新增包的原件映射；原输入已有包先重新校验。若传入的是包含旧包的完整替代数组，可显式加 `--replace-observations`，否则重复包会被拒绝。若新增原件晚于原快照，建立当前新快照，不能回填到旧预测。新版诊断和赛程规则改变后，旧版封存结果须按其原版代码复盘导出；不能改写旧结果使其冒充v10。

命令只创建新输入，不代表已生成或封存预测。仍由 `engine.jingcai_predictor.predict` 及竞彩API消费；写入账本和开球前生成纪律保持既有要求。

## 验证与当前边界

核心621项通过、1项psycopg依赖检查跳过、1项北单分支检查明确排除；采集器70项通过；阵容及来源专项128项通过，其中本轮新增45项。API采集行为使用合成原件和模拟HTTP验证，附合成离线分页示例，不能当作真实球队统计、付费权限验收或训练样本。

另外比较30份原保存输入的v9/v10离线回放，胜平负完整概率和比分矩阵最大差异均为0，原输入SHA一致，没有追加情报或生成赛前预测。本轮没有商业密钥，因此没有付费接口实时验证；没有再次绕过中国竞彩网此前返回的安全拦截。所有新特征的probability_effect_enabled仍为false，模型准确率未被此次测试证明提高。
