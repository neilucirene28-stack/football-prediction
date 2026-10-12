# 竞彩 v8：球队情报、外援与阵容特征

本轮新增的是实际可运行的输入校验、预测输出、情报文本格式及研究特征导出。不是已训练的外援影响模型，没有设置“有外援胜率+X%”或关键球员缺阵固定扣λ。没有访问服务器或取得真实注册名单/首发数据。

## 如何接入

payload.team_intelligence为jingcai-team-intelligence-v1对象。所有新增路径只在竞彩流程使用；v7输入、让球结算和净胜球离线工具保留。PIPELINE_REVISION更新为jingcai-team-intelligence-provenance-20261011-v8，避免新旧输出混记。

无team_intelligence时，预测结果标记missing，不等同无外援、无缺阵。可只覆盖一方，另一方明确null，标记partial。情报不会增加完整度分数、提高等级或自动生成injury修正系数。

| 字段 | 内容 |
|---|---|
|schema|jingcai-team-intelligence-v1|
|match|home、away、competition、season、kickoff_at；精确绑定预测身份，不翻译或模糊合并|
|evidence|每条有id、source、url、sha256、collected_at、available_at；published_at可选|
|teams.home / teams.away|球队情报对象或null|
|team|精确匹配原payload的球队字段|
|player_id_namespace|原来源球员ID的命名空间；不能跨来源或靠中文同名合并|
|roster_complete|是否有完整注册名单；部分名单不能冒充全队贡献比例|
|reference|history_sha256、match_count、evidence_id；摘要绑定原home_recent/away_recent整个列表|
|players|本来源球员名单，不补造球员|
|lineup|未知为null；否则明确expected或confirmed，starting_ids、bench_ids、evidence_id|
|replacements|确认缺阵与可用替代球员ID、evidence_id；仅描述关系，不设任意价值权重|

球员字段：player_id、name、position（GK/DEF/MID/FWD）、foreign_status（foreign/domestic/unknown）、availability（available/injured/suspended/doubtful/unknown）。本契约的injured指已确认不能出场；尚未确认能否出场用doubtful。已知注册身份需要registration_evidence_id，已知可用状态需要availability_evidence_id。

国籍不推导foreign_status；注册身份必须使用当季赛事的注册证据。registration_scope必须精确为team、competition、season；提供赛季统计也必须带此作用域，不能把转会前球队的统计静默算到当前队。season_minutes、season_goals、season_assists缺失用null，不填0；提供数值需season_stats_evidence_id。

reference_minutes_per_match为参考近期战绩窗口中常规90分钟平均上场分钟（0～90）；缺失用null。已完整提供的名单总平均分钟不得超过990。reference绑定原近期列表，但并不证明已与strengths中主客场筛选/时间衰减完全对齐，后续训练及矩阵整合仍须处理该差异。

## 首发与外援使用

confirmed首发必须11名不同球员；expected允许不完整名单。所有首发与替补ID必须在同一名单内且不重叠。确认缺阵球员与首发/替补列表冲突时拒绝预测，需重新核验；不根据信息先后自动猜哪条正确。

anticipated_minutes是调用方对本场常规90分钟出场时长的估计，不是实际已踢分钟，也不会由“确认首发”自动填成90。必须覆盖已列首发/替补，每人0～90，合计≤990。

minute_allocation_complete=true时，必须有完整名单与11人首发，分钟映射覆盖全部注册球员；未计划使用者显式填0，名单外球员不能有正出场分钟。未声明完整时不计算全队外援分钟变化，避免把缺项当0。

结果分开显示foreign_starters_known、foreign_starters_unknown、foreign_starters_exact、observed_foreign_roster_n、阵容状态。11人和注册身份不完整时exact为null。确认首发表示赛前名单中的首发安排，不保证最终出场分钟。

## 输出与研究特征

预测结果team_intelligence保存当前证据摘要、比赛、球队摘要与特征，同时原输入摘要包含整份情报；input_audit中也可见校验结果。format_team_intelligence(pred)提供独立依据行，不修改五个竞彩玩法和稳胆排序。调用方需要显式把该依据行接到报告。

特征包括外援参考分钟、本场预计外援分钟、变化值，已记录确认缺阵球员的参考分钟、赛季进球/助攻贡献比例、缺阵门将观测数、首发与参考阵容的连续性。缺数据保留null；部分名单的观测数只是下界。

全队贡献比例仅在完整名单、相应统计完整、可用性没有unknown/doubtful且全队总贡献大于0时计算。参考窗口和赛季统计是不同口径，分别保存，不能混称“当前λ下降比例”。分钟总变化主要是覆盖诊断，不替代对位置、球员价值及替代质量的建模。

当前probability_effect_enabled始终false。特征模块未改λ、比分矩阵、胜平负、让球、半全场或MC目标；正常出场的主力外援已体现在近期战绩中，后续应学习阵容变化的增量，不重复加成。

## 时间和来源

任何嵌套对象声明的available_at/collected_at/published_at/feature_as_of晚于预测快照都会拒绝。时间带时区；collected_at不得晚于available_at，published_at不得晚于collected_at。缺证据、作用域、参考摘要不匹配直接报错，不把坏情报静默转成正常数据。

引擎只核验传入声明及其绑定，不能证明HTTP正文真的说了这些信息，也不能认证历史采集时刻。SHA256声明不是来源鉴真。新增导入器可对照保留的HTTP原件检查字节摘要，但仍不能仅靠本地文件证明历史采集时间或正文语义；输出中明确caller_declared_evidence_not_source_authenticated。

```bash
python3 scripts/import_jingcai_team_intelligence.py \
  --payload /path/to/prematch-payload.json \
  --intelligence /path/to/team-intelligence.json \
  --evidence-files /path/to/evidence-paths.json \
  --output /path/to/new-enriched-payload.json
```

evidence-paths.json是证据ID到本地原件路径的JSON映射。本工具不抓网络、不覆盖旧文件、不重写snapshot_at，不创建或补造赛前预测。导入历史快照只能作离线检查；不能将赛后采集的情报填成历史赛前可得。

## 研究导出

使用既有只读账本导出器取得一个明确竞彩版本的真实输入/结果/结算，再运行：

```bash
python3 scripts/export_jingcai_team_features.py \
  --input /path/to/jingcai-version-ledger.jsonl \
  --output /path/to/team-features.json
```

检查原输入摘要、真实赛前时间、结算来源与可得时间、存储情报是否与原输入重建一致。未知特征保持null。导出记录不等于独立训练比赛；同场不同预测、重复和冲突需在学习阶段处理，版本及expected/confirmed阵容组分别验证。

下一阶段仍需学球员价值/替代质量，与不含情报的基准做按时间划分的增量验证，要求Brier不恶化、检查进球均值偏差，再统一整合比分矩阵及全部衍生玩法。没有真实数据时不启用固定12.5%缺阵扣分等旧试验规则。

## 已运行检查

- 41项新增合成契约测试，包括注册、首发、替代、缺失、未来时间、原件摘要和账本绑定。
- 534项竞彩及通用检查通过，1项psycopg缺失跳过、1项范围外用例排除；采集器70项通过。
- 昨天30份原输入摘要核对并离线回放：全部missing情报，完整概率与完整矩阵最大差异均为0；没有追加新赛前预测。
- 194场旧历史研究导出0条，全部缺原输入；没有补造阵容、外援、时间或训练样本。
- Python编译通过；线上数据库与部署未执行。
