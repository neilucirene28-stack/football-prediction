# BD-1 独立北单影子基线（P0）

状态：**已实现可执行的 L3 根/赛事族先验、开售池只读导入和赛前快照；未训练生产参数，未做真实北单赛前 walk-forward，禁止上线**。包仅用 Python 标准库，不导入竞彩 `engine.predict()`。设计全文见 `docs/beidan-independent-model-v1-design.md`。

## 输入

`predict_l3(history, asof_at, kickoff_at, competition_family, handicap)` 需要逐场已结算常规时间历史：

```json
{"match_id":"past-1","competition_family":"adult_cup","kickoff_at":"2026-09-01T10:00:00+08:00","result_available_at":"2026-09-01T13:00:00+08:00","regular_time":true,"ft_home":2,"ft_away":1,"ht_home":1,"ht_away":0}
```

预测仅使用 `kickoff_at < asof_at` 且赛果时间有证据的比赛。有原始 `result_available_at` 和本系统 `verified_at` 时取较晚者；原始发布时间未知、但今日新核验的旧赛果可填真实 `verified_at`，仅用于此刻以后的预测，**不能用于历史时点的 walk-forward**。至少 30 场过去结果；没有半场比分、赛果可用/核验时间或比赛身份时不能伪造。26102 Markdown 预测摘要及 26103 未核验时间的影子预测不是历史训练输入。

Muse 在 GitHub v2 提交 `82286b1` 导出 1665 条历史赛果。`load_verified_history(path)` 可载入该 JSONL 的本地副本，仅保留期号/场号、常规时间比分、开球与真实首次核验时间；未证的 SP、让球线与赛事族都不进入特征。只允许 `asof >= verified_at` 的未来请求消费这批历史。GitHub 文件结构已核验，但比分的外部权威来源尚未逐场独立核对；因此仍是影子用途。

赛事族尚无人工核验映射时传 `competition_family=None`，只使用根先验并输出 `family_status=unknown_root_fallback`。`load_offered_pool(shadow_path, skipped_path, expected_total=193)` 将26103的111条旧影子记录与82条跳过记录合成开售池清单，校验唯一期号/场号、状态和场数。开球字符串按原字段文档所称北京时间转为带时区形式，并注明该时区依据尚未独立核验。导入时丢弃无来源时间的SP/让球线，所有旧记录仅作观察。合并后的193场仍需与权威开售赛程交叉核对，才能称权威全池覆盖。

## 输出与方法

- 从过去比分拟合 Poisson 根先验，以同赛事族的历史比分和根先验做收缩；当前 `kappa=50`、`min_history=30` 只是待验证候选。
- 从过去半场/全场进球估计半场进球比例，条件于全场比分做二项分配，得到九项半全场联合概率。该条件模型也是待验证候选。
- 同一个全场比分矩阵导出三项胜平负、整数让球三项、31 类全场比分、八项总进球和四项上下单双。比分完整矩阵另存 `vectors.score`，31 类项目向量为 `score_31`。福建体彩公布的单场**全场比分**规则是31类（主胜13、平5、客胜13），25类属于其他玩法；具体期次仍须核对实际开售选项。
- `handicap=None` 时，让球向量返回 `null`；不会把未知线当零线。
- 输出 `parameters_unvalidated=true`，不会调用旧北单 Platt 负斜率、旧弱赛事封顶、市场融合或阵容修正。

## 保存快照

`build_snapshot(...)` 要求模型版本、六玩法完整向量、逐场开球/请求/生成时间和实际使用来源。v4增加 `input_sources` 与 `training_lineage`，将实际用到的 `fixture`、`history`，以及适用的 `family`、`handicap` 字段绑定到来源名称；缺绑定的旧格式只能标为仅观察。来源时间缺失时自动记为 `observation_only=true`、`as_of_backtest_eligible=false`。来源时间晚于请求时点、预测生成晚于开球、概率不归一或衍生向量与比分不一致则报错。`save_snapshot(directory, record)` 用独占创建写盘；同场更新首发后可用新的真实生成时间另存一版，旧版不能覆盖。仅允许来源名称、来源比赛 ID、可用时间和状态，拒绝来源结构中额外密钥等字段。

调用方必须从可信时钟传入真实当前 `generated_at`（生产调用省略参数自动使用 UTC 当前时间），并从采集日志提供真实 `available_at`。旧文件 mtime 不等于数据采集时间。


## 独立影子运行

`python -m beidan_bd1.runner --history results.jsonl --fixtures offered.jsonl --period 26103 --expected-total 193 --out run-output`。`offered.jsonl` 必须为完整输入池；每场至少有带时区 `kickoff_at`、`period`、`match_id`，并可附 `sources` 和 `input_sources` 指向实际采集证据。程序使用运行时真实UTC时钟，逐场保存完整概率向量、训练赛果ID及最晚可用时间；未证的赛程来源一律仅观察，未证的让球线不参与预测。每场无论成功或跳过都有状态和原因；全池账本先独占预留run_id，最终独占写入manifest。账本只称 `supplied_roster_unverified`，还需独立核验官方完整开售赛程。测试用 `synthetic_at` 会强制全部 `observation_only=true`。

以已导出的1665场和26103的111+82条作一次合成时钟端到端演练：历史1665/1665导入、赛程193/193去重通过，任选一场未来赛事成功生成不可覆盖快照；因旧赛程和赔率来源时间缺失，该记录 `eligible=0`。这只是链路测试，不是比赛效果验证。

## 验证

从 `beidan_review` 目录运行：

```sh
PYTHONPATH=. python -m unittest discover -s tests -p 'test_beidan_*.py' -v
```

29 项检查覆盖未来赛果隔离、常规时间筛选、历史导入的时间门控、无历史拒绝、六向量自洽、输入字段来源绑定、旧赛程合并、训练历史血缘、合成时间门控、完整赛程运行账本、来源时间缺失降级、赛后生成拒绝、不可覆盖写入及成对 Brier 审计。`audit_frozen_pair()` 要求两个模型在同一赛前决策时点和相同场次上计分，输出全开售池覆盖率；该函数只评估冻结预测，明确标为 `paired_asof_audit_not_walk_forward`，不会把一次计分冒充参数训练的滚动验证。上线仍需真实赛前快照的赛事族分层滚动验证，比较冻结基线的三分类 Brier（不得恶化）、比分 log loss、让平/平局校准以及全开售池覆盖；当前没有可复跑的北单赛前 payload，不能报告模型收益。
