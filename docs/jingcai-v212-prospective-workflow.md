# 竞彩 v2.12：统一入口、不可变留档与公平复盘

本轮继续完善竞彩流程。API 与批处理现在调用同一个 `predict_jingcai` 入口；默认不自动加载历史校准。复盘接口与评分看板共享最早记录选择规则，并对不同版本分别评估。没有改变进球模型的结构参数，没有启用 gamma=0.8 研究候选，也没有生成新的真实比赛预测。

## 入口与校准策略

过去 API 不启用旧 Platt 参数，而 batch 会在没有赔率时自动启用 `engine/calibration.json`。同一输入因此可能因入口不同而得到不同概率。本轮移除 batch 的自动加载，统一使用环境配置与默认未校准策略，输出明确的 `calibration_policy`，并将策略写入版本参数摘要。

生产入口拒绝传入历史 Platt 参数；独立研究仍可以明确调用底层引擎回放。旧参数文件和十二场原预测没有改写。历史文件只写明曾使用随机5折CV，未提供可独立核验的拟合数据和时间证据，所以本轮不把它自动推广到另一个生产入口。

这项变化会改变旧 batch 的部分无赔率预测。它是统一策略与验证纪律的调整，不能据此宣称命中率提升。已有市场融合和默认未校准概率保持原算法。

## 最早预测与版本隔离

|情形|处理|
|---|---|
|同一 match_id 和 model_version 多次预测|只保留最早合格生成时间；后续重复记录计入审计分母|
|最早记录没有赛果，后来记录已经结算|最早记录继续 pending，不替换成后来记录|
|最早时间完全相同而概率不同|整组隔离，避免任意挑选|
|同一身份的开球时间发生冲突|整组隔离，等待身份/改期核验|
|生成时间、数据库 predicted_at、输入快照不一致|排除并报告原因|
|缺少全精度概率、保存概率与原向量不一致|排除，不用展示四位概率补造原记录|
|历史回放、旧记录缺少真实生成元数据|观察记录保留在原表，本轮严格评估不计入|
|赛果为负数、只缺一方、结算时间在开球之前或未来|所选预测保持 pending；不改选后来的预测|
|同场不同模型版本|分别评分、校准分桶、计算影子权重，不给出跨版本合并 Brier|

数据库查询使用 LEFT JOIN settlements，先包含所有候选预测，再选择最早记录。滚动窗口按 kickoff 限定，不再对预测行使用 LIMIT，以免把真正最早的记录截掉。API 的 n 是已结算的“比赛×版本”数量，独立比赛数位于 audit.unique_matches。

这些规则检查数据一致性，没有认证历史数据提供方。报告始终返回 `independent_source_provenance_verified=false`，不能将其误当作严格来源验证或正式选模验收。

## 不连接数据库也能留档

API 与 batch 对成功的竞彩预测写入独立 UUID 目录，保存两份新建文件：

- forecast.json：完整输入、全精度输出、模型版本及实际生成时间。
- receipt.json：原文件的 SHA256、字节数、文件落盘时刻、开球时刻和状态。

使用独占创建，预测文件 flush/fsync，目录 fsync 后记录实际 durable_at。每次重跑新建记录，不覆盖旧记录。若预测文件写完时已开球，保留原件并标记 late_write；校验器拒绝把它作为赛前落盘记录。文件损坏或摘要不符也被审计器报告，原件保留。

API 单独显示本地 archive 与数据库 persistence 状态；无数据库可以有本地留档，但不会冒称数据库保存成功。默认目录是 data/snapshots/jingcai，可通过 JINGCAI_ARCHIVE_DIR 配置。权限/磁盘/fsync 失败会明确返回 archive failed。

本地摘要和机器时钟不是独立签名或第三方时间戳，也不证明输入赔率、伤停或战绩在当时真实可用。不能依靠本地 receipt 宣称来源已经获得严格认证。

## 验证

`python -m pytest tests collector/tests -q --ignore-glob='tests/test_beidan*'`：374 passed，1 skipped，3.92s。跳过的是未安装 penaltyblog 的影子模块。测试使用合成数据与临时目录，不生成或上传真实预测。

新增检查包括：API/batch 同一入口、生产参数与历史参数隔离、摘要篡改检测、开球后落盘拒绝、重复预测选择、跨版本隔离、时间/概率不一致排除、提前结算和身份冲突处理。

`python scripts/jingcai_archive_audit.py` 在当前工作区报告 0 个真实归档，没有把合成测试样本加入实际预测目录。

本轮没有重新拟合参数或重新宣称准确率提升。十二场复盘及119训练/30验证/75留出的224场历史校准实验沿用 v2.11 已记录的独立研究报告：docs/jingcai-v211-review-and-flow-audit.md。那次 gamma=0.8 候选仍未启用。

## 环境验收与后续边界

当前没有 DATABASE_URL，也没有本地 PostgreSQL，真实数据库端到端验收仍未执行。查询、保存、失败回滚等分支通过合成行及 mock 测试，不会把它们说成生产数据库验证。

统一 HTTP 原始响应与来源身份/available_at 留证还没有接到竞彩全链路。旧日期专用脚本保留为历史用途；持续生产使用统一的 API/batch 入口。需要部署后的新比赛样本，才有资格继续评估校准和参数迭代。

## 复现

```bash
python -m pytest tests collector/tests -q --ignore-glob='tests/test_beidan*'
python scripts/jingcai_archive_audit.py
python scripts/jingcai_archive_audit.py --directory /your/forecast/archive
```

上传分支仅 codex/jingcai-v211-model-review，基于 v2。不要更新其他项目的分支或 PR；交接包内同时提供全量增量与从上一版升级的补丁。保持草稿，不合并，不部署。
