# 竞彩第九轮：球队情报接入

本轮完成来源采集/原件导入、身份与时点校验、结构化特征、预测输出和赛后研究导出。只属于竞彩。`engine.jingcai_predictor.predict` 拒绝北单项目、参数和字段；本轮不改北单计算分支。没有部署远程服务，也没有生成当天投注建议。

## 来源实际接入情况

| 来源 | 已实现入口 | 本次验证与限制 |
|---|---|---|
| 中国竞彩网 sporttery.cn | 单次请求、失败原件归档、离线伤停表结构解析、显式球员映射；同类信息优先选择 | 官方网站本次 HTTP 567 安全拦截，未取得伤停正文。八列表解析经过合成契约测试，未完成当前官方正文的真实结构验收；变化时返回 unsupported。无原生球员ID时不可按译名自动绑定 |
| ESPN | summary 实时请求、原生 event/team/athlete ID、开球时间及赛前状态校验 | 本次原生 event 401879266 请求成功，但尚未发布名单，记 missing。澳客 mid 禁止作为 ESPN event ID；未验证该原生事件对应哪条竞彩编号 |
| API-Football | 密钥通过请求头传入；lineups/injuries/players 原件适配；查询fixture或统计作用域验证 | 未提供密钥，未实测付费权限。多页统计拒绝冒充完整名单，须归档全部页面后构造规范化包；查询参数及源内球队ID不是对竞彩身份映射的独立认证 |
| Sportmonks | fixture 原件离线适配；赛事/赛季/球队/开球/赛前状态核验；572 confirmed 元数据 | 未提供付费权限；有阵容但没有确认元数据时保持首发未知；false为预计，true且11人为确认 |
| Premier Injuries | 实时请求入口及离线原件解析；原生 team/player ID绑定；修正状态和移动端列标题 | 原留存网页解析104条；仅按condition/status分类为59伤病、36成疑、9可用，未据此宣称比赛级完整伤停覆盖；保留不带年份的原始新闻文本，不猜年份 |
| J联赛、K联赛、英超/英足总、MLS、中国足协 | source whitelist + 同一规范化证据包导入 | 已接校验与特征消费，未编造通用网页抓取器；停赛须核实适用具体赛事、赛季和比赛。K联赛合作API需授权 |
| Transfermarkt、Opta、Wyscout、StatsBomb | 规范化原件导入、来源域名及时点校验 | 原件需另存并逐一核SHA；商业权限和联赛覆盖未经此次验证 |
| FotMob | 授权原件规范化导入；离线 `squad.squad[].members` 展开 | 留存队伍原件展开28名球员、排除教练；不新增自动网络请求。包需明确 `authorized_systematic_access: true`，不能把本次离线解析当成授权证明 |

失败来源也进入预测审计状态，不能用失败页面内容构造伤停。请求禁用自动重定向、禁止URL带密钥，不绕过安全拦截。API密钥由环境变量 `API_FOOTBALL_KEY` 提供，不写入日志或示例。采集时间使用实际UTC时间。

## 新输入和特征

输入为 `payload.team_observations`，schema `jingcai-source-observations-v1`、project `jingcai`，包含统一比赛作用域及 `packets`。每包包含 `source/match/binding/status/evidence/teams`；证据有来源、URL、实际采集/可得时间、原件字节SHA256。比赛绑定需赛事、赛季、开球时间及原生event/home/away ID，不能把译名相似当成映射证据。

球队包含原生team ID、等于source的球员ID命名空间、完整名单声明、球员列表和预计/确认阵容。球员包含以下独立信息：

- availability：available、injured、suspended、doubtful、unknown。25%/50%/75%是出战状态标签，不是已校准比赛概率。
- 赛季 minutes/goals/assists/yellow/red：非负整数或null，必须有对应赛事/赛季作用域和证据。API原始 red 与 yellowred 分开，不擅自合计。
- registration：foreign/domestic/exempt/partner/unknown，加规则版本、作用域、名额占用布尔或null。国籍不能证明外援注册或名额占用。
- anticipated_minutes：有证据的预计分钟，确认缺阵必须为0，已知分钟合计不超过990。未知分钟不得填0。

输出 `team_observations.sources` 保留各来源独立球员及特征；`selected_by_side` 选择一般优先来源，`selected_by_dimension` 分别选择可用性、阵容、牌数、注册信息。官方伤停不会抹掉另一来源确认的首发；两份不同命名空间的名单不会按姓名联结。也不能用来源A球员名单和来源B统计构造贡献占比。

主要特征为确认缺阵、成疑数量、缺阵门将、赛季黄/红牌、牌数/player90、缺阵球员分钟/进球/助攻占比、外援首发、外援名额首发、外援预计分钟。当前完整注册名单及所有对应统计都已知时才能计算名单总量；这仍不自动等于含已转出球员的全赛季全队累计。完整可用性未知时不能计算全队缺阵贡献占比。`observed_season_*_sum` 仅汇总本来源已观测球员，缺字段返回null。原始球员数据保留以供研究。

同一来源多个包默认标记冲突并排除该来源的对应侧。只有显式 `--merge-same-source` 才按同fixture、同球队、同赛季、同命名空间和球员ID合并；改写证据引用防止ID碰撞，并保留原包摘要。已知状态、统计或名单冲突拒绝导入，不自行选择较新的值。合并前后均执行校验。

## 使用方式

在项目根目录运行。绑定文件为JSON对象：`{"match":{"home":"…","away":"…","competition":"…","season":"…","kickoff_at":"带时区时间"},"binding":{"event_id":"原生ID","home_id":"原生ID","away_id":"原生ID","league_code":"ESPN联赛代码（适用时）"}}`。API统计还需 `competition_id/season_id`。所有省略号需换为已核验的真实值。

实时请求并建立新的输入快照：

```bash
python3 scripts/import_jingcai_observations.py --payload input.json \
  --collect espn --binding binding.json --capture-dir captures/unique-run \
  --refresh-snapshot --output enriched-input.json
```

其他单次采集可指定 `sporttery`、`premierinjuries` 或 `api_football --kind api-injuries`；API类型可为api-lineups/api-players。新目录/新输出文件必须不存在，防止覆盖既有记录。

留存原件导入：

```bash
python3 scripts/import_jingcai_observations.py --payload input.json \
  --raw response.body --meta response.meta.json --binding binding.json \
  --kind espn --output enriched-input.json
```

规范化多包（含其他官方/商业来源）导入：

```bash
python3 scripts/import_jingcai_observations.py --payload input.json \
  --packets packets.json --evidence-files evidence-files.json \
  --merge-same-source --output enriched-input.json
```

`packets.json` 是包数组；`evidence-files.json` 是 `{"0:http":"captures/first/response.body","1:http":"captures/second/response.body"}`，键为合并前包下标和证据ID。必须逐证据指向真实原件；规范化输入文件本身不能自证来源。若原件晚于原快照，必须建立当前新快照，绝不回填旧预测的赛前输入。

脚本仅生成校验后的新输入，不代表已预测、已写入账本或已结算。将该输入交给 `engine.jingcai_predictor.predict` 或既有竞彩API，结果即包含情报、五玩法及独立审计。`format_team_intelligence` 展示来源/状态，赛后 `engine.jingcai_team_features.extract_rows` 重新校验封存输入和结果一致性后导出研究行。

日采集的竞彩ESPN段改为读取 `data/jingcai_source_bindings.json`，内容是 `{project:"jingcai",source:"espn",match:{…},binding:{…}}` 的JSON数组。没有显式映射或已开球即跳过，不再从澳客board ID猜ESPN ID。`espn_lineups.json` 新形状为带scope的packets归档，不会未经校验自动注入预测；其证据文件映射仍需按上述规范化导入命令提供。

## 概率作用与验证

`probability_effect_enabled` 恒为false。完成的是输入、特征和输出接入，尚未把人数或牌数硬编码成胜率/λ调整，也不把旧injury字段伪装成新证据学习的参数。数值系数需要足量真实赛前封存样本、赛后结算和按时间拆分的增量验证，目前不具备该训练集。

核心576项通过、1项因缺psycopg跳过、1项北单分支检查明确不参与竞彩验收；采集器70项通过。新增42项合成情报契约测试不证明准确率提高。另对30份原保存输入以相同配置比较v8/v9离线回放，胜平负完整概率和完整比分矩阵最大差异均为0；未新增情报、未创建赛前预测、未改写历史账本。测试记录和真实来源解析状态随代码包提供。
