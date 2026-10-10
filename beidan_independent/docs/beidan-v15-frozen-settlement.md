# BD-1 v15：原赛前概率封存与赛后结算

本次以草稿PR #2的v14提交 `c4712fc3dd44e7ec4a8d8531f6a11c813c618667` 为基础继续代码评审；433个独立目录文件逐个Git blob摘要匹配。当前104项标准库unittest通过。

## 修复的问题

1. v13/v14文档的赛后流程会再次调用walk-forward，按当时的代码与历史重新拟合。它可以做历史重放，但不能替代原冻结预测的实际赛后计分。新结算器只读取已保存概率，保留原选择，不调用拟合器；所有预声明候选的成绩只作为诊断，不在赛后选择“赢家”。
2. 原规范ID导入器输出字符串identity_source、独立审核时间、sources列表和逐项input_sources，而walk-forward把identity_source当来源对象处理，导致已审核的规范身份也被拦截。新门控兼容规范导入格式，继续拒绝晚于cutoff的审批、未绑定的ID和重复来源。来源ID研究格式保持单独核验。
3. 原冻结缺少可独立保留的整包字节清单。新冻结器写完四份输入后记录真实封存时间和SHA256。封存或生成完成时已开球就拒绝；清单独占创建，不覆盖旧文件。

## 实际验证

- 新增16项回归测试，104项全量测试通过。
- 测试覆盖计分时拟合器被禁用仍能完成、原文件不改变、错绑主客/赛事/赛季/阶段拒收、重复或池外赛果拒收、原文件及清单篡改发现、半场缺失与时间门控、晚于开球不能封存、以及规范身份导入格式。
- 新赛前冻结使用144场已核验来源历史（德乙54、英甲90），完整输入池179场，17场研究候选、162场阻断。原v14冻结保持原字节。
- 实际cutoff：`2026-10-09T13:50:23.903669+00:00`；封存时刻：`2026-10-09T13:50:28.876075+00:00`。生成完成和落盘封存分别记录，不能将开始运行时间充作生成完成时间。
- 冻结目录：`outputs/l1_prospective/20261009T135028491435Z/`。
- 独立保留的freeze_manifest.json SHA256：`8cf95372822fed74b7c028d6679636bc9cbc55c601de4094df6386f197155d37`。
- 对此冻结运行空赛果结算：179场完整账本、17场待结算、162场原冻结阻断、0成对赛果；Brier和差值为null，production_gate_passed=false。结果在`outputs/v15_pending_settlement.json`。

## 复盘命令

在独立目录运行：

```sh
PYTHONPATH=. python -m unittest discover -s tests -p 'test_beidan_*.py'
```

当前冻结已有清单，无须重复seal。以后新冻结由freeze_native_pool.py自动封存；对于仍未开球的旧冻结，可一次性运行：

```sh
PYTHONPATH=. python -m beidan_bd1.frozen_settlement seal --bundle NEW_BUNDLE
```

赛后取得真实ESPN summary原件，以下导入器保留本次读完输入的实际核验时间，严格核对来源ID和90分钟结束状态；不从进球事件推算半场。每个--payload指定一份新原件，可以重复指定：

```sh
PYTHONPATH=. python -m beidan_bd1.frozen_results \
 --bundle outputs/l1_prospective/20261009T135028491435Z \
 --manifest-sha256 8cf95372822fed74b7c028d6679636bc9cbc55c601de4094df6386f197155d37 \
 --payload NEW_VERIFIED_ESPN_SUMMARY.json \
 --out outputs/v15_verified_results_NEW.jsonl

PYTHONPATH=. python -m beidan_bd1.frozen_settlement settle \
 --bundle outputs/l1_prospective/20261009T135028491435Z \
 --manifest-sha256 8cf95372822fed74b7c028d6679636bc9cbc55c601de4094df6386f197155d37 \
 --results outputs/v15_verified_results_NEW.jsonl \
 --out outputs/v15_settlement_NEW.json
```

两个NEW输入均指未来实际取得的文件，当前没有这些赛果。每次结算输出独占创建；新赛果文件另存，不写回原空results.jsonl。清单摘要须在封存时另行保留，不能每次计分时从可能被修改的清单重新计算并自动接受。每次新增结果后可再次对同一概率结算到新报告。

## 计分与边界

胜平负Brier、31类比分log loss、进球均值误差都按原保存概率计分。还保存总进球、上下单双、可得时的整数让球及显式半全场成绩；没有线或半场就返回null。原冻结阻断比赛始终留在覆盖分母中，取得其赛果也不能补造预测。冷启动选择None，不把研究表ridge=5的预测冒充已选模型。

SHA256只能检测相对独立保留摘要的字节变化，不能认证提供商、时钟或中文身份审批。新原件的真实性和可得时间仍需外部采集日志审计。模块不授予生产资格，也不把一次冻结计分冒充历史walk-forward。

目前仍缺162场足够的已核验球队历史或fixture绑定，新鲜官方北单整数让球/SP证据及中文规范绑定审批；0场已结算配对，尚无准确率提升或Brier不恶化证据。原至少5期/500场、Brier/比分log loss/均值及完整池门槛继续适用。未来多期汇总还须去除同一比赛的重复冻结并严格区分赛前选择与赛后诊断。

