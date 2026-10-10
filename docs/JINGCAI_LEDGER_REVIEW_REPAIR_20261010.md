# 竞彩第三轮修复：预测账本、复盘评分与校准来源

本文记录第三轮阶段实现与当时兼容性验收。按用户要求，随后已切换到竞彩专用入口、撤回北单专用测试改动并独立报告竞彩验收；当前导入与入口以`JINGCAI_PROJECT_SEPARATION_20261010.md`为准。

本轮直接修改上传项目源码。第三版交付包是对原始上传代码的累计补丁，包含前三轮修复；已应用前版者按差异合并，不要重复应用累计补丁。未部署服务器，未访问生产数据库。

## 已确认的问题与修复

| 问题 | 当前行为 |
| --- | --- |
| 预测结果可与另一份输入拼接后封存 | 竞彩结果携带调用方原输入的SHA256；写入与复盘均校验摘要、球队、开球时间及模型版本 |
| 生成后拖到开球才写库，仍可能记录为赛前预测 | Python校验当前时间，SQL使用`clock_timestamp()`在实际执行时再次检查开球门槛；未写入必须报告失败 |
| 幂等键命中不同快照仍返回“成功” | 检查原封存输入摘要与版本；不一致报失败，保留原记录 |
| API吞掉写库错误，输出无法判断是否封存 | 输出`persistence`状态；未接库、写入失败分别标明原因，只暴露异常类型 |
| 复盘接口读取没有对应真实预测的旧结果表 | 改读`predictions JOIN settlements`，审核完整原输入与预测，再按版本计算 |
| 同场多次预测、多个版本被混为独立训练样本 | 每个`match_id + model_version`保留最早合法赛前记录；版本独立，另报真实比赛数和版本观测数 |
| 某信号缺失时仍与其他信号不同样本比较 | 模型与市场/Elo只在相同可得样本比较；权重诊断按信号可得集合分组 |
| 同开球时间多场比赛只累计一次时间权重 | 每场比赛累计一次衰减样本量，不按开球时间去重 |
| 复盘只覆盖胜平负和让球，部分指标使用舍入概率 | 全精度计算胜平负、让球、总进球、半场、半全场、双方进球及期望进球误差；缺失玩法分别记录分母与原因 |
| 半场结果缺失可能被默认成0:0 | 未提供、仅一方提供、非法或大于全场的半场比分均不评分半场/半全场 |
| 无版本与时间来源的旧Platt参数被脚本直接加载 | 自动加载旧参数被禁用；显式候选必须通过基础模型版本、信号范围和时序来源检查 |
| 批量脚本重新构建Poisson矩阵，或将亚盘线当竞彩整数让球 | 5个脚本统一读取预测器最终让球结果；正式竞彩让球在预测前注入；没有正式让球不编造让球玩法 |

## 账本与时间约束

`engine/jingcai_ledger.py`要求竞彩结果为`ok/live`，具备明确版本，并满足：

```text
snapshot_at = feature_cutoff_at ≤ evaluation_asof ≤ prediction_generated_at
prediction_generated_at ≤ 封存校验当前时间 < kickoff_at
```

输入摘要绑定原始调用输入，而非归一化后副本。历史重放仍可用于离线分析，但不能进入真实赛前预测账本。SQL写入使用已有表结构，无迁移；`ON CONFLICT DO NOTHING`仍保持预测不可变。SQL门控未插入且不存在同内容原记录时回滚并报告失败。该门控依赖数据库服务器时间；本次仅验证SQL生成和失败分支，未连接真实Postgres验证时钟或锁行为。

同日幂等重复请求仅在原输入与版本一致时返回已有记录ID。批量输出标为`stored_or_existing`，API输出写入状态与ID；未存储预测不得被看作已封存预测。摘要证明记录内容绑定，不能证明外部采集来源真实，也不能防止拥有写库权限的人伪造历史记录。

## 复盘口径

`scripts/scoreboard.py`和`GET /api/backtest/summary?days=30&version=...`共用`engine/jingcai_review.py`。接口最多读取最近2000条记录，响应明确报告此覆盖范围；不是全库汇总。CLI不使用这一接口上限。

记录需有可校验的原输入/预测、合法比赛ID、相同版本、对应全场赛果，且开球和结算时间均不晚于评估截止。拒绝其他项目、历史重放、赛后生成、未来结算、NaN/Inf概率和错配快照，并输出排除原因数量。

旧记录若缺SHA256但有完整且合规的原快照与时间，可以描述评分；不进入权重学习诊断。带摘要且具有结算来源标记的合格记录才进入影子权重诊断；这仍只是来源声明校验，不是独立鉴真。

指标使用`multiclass_brier_sum-v2`：多分类Brier为各类别误差平方之和，不能与旧的除以3口径直接比较。显示值仍可四舍五入，聚合使用完整精度。每项指标的`*_n`分别报告实际可评分样本数，缺半场或比分列表不会当作预测错误。

输出覆盖：

- 全场/让球胜平负Brier、LogLoss、命中率，普通平局和让平的二分类Brier。
- 总进球0～6和7+分桶的Brier、LogLoss、命中率。
- 有真实半场赛果时的半场三分类和半全场九分类指标。
- 双方进球Brier、期望总进球绝对误差及有符号偏差、可得比分Top3/Top5命中率。
- 按版本、联赛的样本分组；市场/模型和Elo/模型同样本比较；三类胜平负校准分箱及ECE。

影子权重按照相同信号集合、同一版本和相同比赛计算衰减损失。90天半衰期，每场累计一次；衰减样本量不足15时不给权重。这些权重由所展示样本的损失拟合，**没有独立样本外验证，也不会写回生产配置**。不能据此声称学习效果、命中率或收益提升。共享北单`engine/adaptive_weights.py`未修改。

## Platt参数入口

`engine/calibration.json`原文件保留。原文件的随机折交叉验证、日期和样本数不足以证明当前基础模型的赛前时序校准来源，5个脚本自动加载它时返回禁用原因，不再静默套用。

新候选通过`platt`与`platt_provenance`提交，来源字段至少包含：

| 字段 | 校验 |
| --- | --- |
| `model` | 必须为`jingcai` |
| `base_model_version` | 与本次不含Platt的基础模型版本完全一致 |
| `scope` | 有市场信号为`market_fused`；无市场但有Elo为`elo_fused`；均无才为`form_only` |
| `training_results_available_before` | 带时区的训练结果可得截止 |
| `validation_results_available_before` | 晚于训练截止的验证结果可得截止 |
| `fitted_at` | 不早于验证截止，且不晚于预测评估截止 |
| `validation_kind` | 必须声明`chronological_holdout` |
| `training_rows_sha256` | 64位小写十六进制SHA256 |
| `n_train` / `n_validation` | 正整数，不接受布尔值 |

元数据只校验声明的一致性，没有独立读取训练记录或重做拟合，输出明确标为`declared_provenance_checked_not_independently_authenticated`。需要在真实开发环境另行核对原训练记录、严格时序划分、留出评分及参数文件。本轮未拟合新Platt参数，也未启用第一轮审计中效果更差的研究候选。

预测输出新增`base_model_version`、`calibration_audit`与`signals_full`，并补充总进球精确分布、双方进球和期望进球的完整精度字段。流水线修订号为`jingcai-ledger-review-consistent-20261010-v3`，改变竞彩版本哈希；旧版本账本不会与新版本混合学习。

## 批量脚本

统一接入`engine/jingcai_batch.py`和校准入口的脚本为：`batch_predict_today.py`、`predict_today_0930.py`、`rerun_xiaodianhuo.py`、`rerun_xiaodianhuo_remaining.py`、`predict_jingcai_1005.py`。原单次日期脚本不会自动获得新的真实赛前数据，需要操作方确认输入日期；本次没有联网运行这些脚本。

批量脚本完整保留原输入与引擎结果，使用上海时区生成同日幂等日期。`fixture.rq`必须为竞彩整数让球，与已存在的`handicap_1x2`冲突则拒绝。未提供正式让球时返回空让球结果；亚盘数据仍保留为亚盘玩法。

## 实际验收

- 核心`tests/`：**460项通过，1项跳过**，4.28秒；跳过原因是未安装penaltyblog。
- `collector/tests/`：**70项通过**，0.06秒。
- 本轮新增44项用例：输入绑定、晚封存、SQL时间门控失败回滚、不同快照幂等冲突、接口写入状态、版本隔离、同场去重、缺失信号同样本比较、未来赛果隔离、非法概率、半场缺失/非法、各玩法分母、校准来源约束及真实批量入口的离线集成。数据库与Titan来源均使用测试替身。
- 12组合成回归：6组北单完整输出与原上传源码一致；6组竞彩默认λ、胜平负和已有衍生概率与第二轮完全一致；半全场边际最大误差为7.7715611724e-16。不是实际赛事收益或命中率回测。
- 原194场回填经新复盘CLI检查：194条均因`missing_original_payload`排除，0条进入账本评分/学习。离线审计仍保留，不补造原预测时间或输入。
- 累计补丁在本地原始受改文件副本上检查应用，并逐文件核对SHA256；交付包保存对应验证结果。

当前验收尚不包含真实Postgres、线上API部署或真实来源的赛前封存链路。MUSE导入后需要在自己的分支运行测试，再用实际测试库验证开球门槛、幂等冲突、事务回滚、数据库返回行类型及复盘接口。

## 重现命令

在项目根目录执行；数据路径替换为解压包内真实路径：

```bash
python3 -m pytest tests/ -q -rs
python3 -m pytest collector/tests/ -q -rs
python3 scripts/scoreboard.py --input /path/to/audit_input/hidden_backfill_jingcai.jsonl --json-output audit_output/jingcai-letdraw/ledger_readiness.json
python3 scripts/audit_jingcai_letdraw.py --output audit_output/jingcai-letdraw
python3 scripts/audit_jingcai_grid.py
python3 /path/to/verification/compare_baseline_probes.py --project-root .
```

离线账本输入需要JSON数组或JSONL，单条包含与`load_review_rows`同名的字段，`payload`为`{"input":原输入,"result":原预测}`；还需版本、比赛ID、预测/开球/结算时间、保存的胜平负概率和赛果。缺证据时应保留排除结果，不用现在重算的预测替代原封存。
