# BD-1 v13：球队强度walk-forward与均值偏差资格链

## 当前结果

本次继续实现和验证模型，未改动外部仓库。

- 独立北单测试80项通过。
- `run_walk_forward` 增加 `model_family=l1_team_strength`，每折重新拟合球队强度，候选ridge预先固定为2/5/10/20，None保留L3基线。
- 参数只依据更早测试期且在当前cutoff之前已实际可得的赛果Brier选择。冷启动30场选择样本之前不选L1；平分优先None。
- 来源ID研究模式和规范身份模式分开。球队/赛事阶段有独立可用时刻，不能用赛程日期替代身份审核时间。
- L1计分赛果必须逐项绑定来源比赛/主客/联赛/赛季/阶段ID，或规范主客/赛事ID。历史与计分结果若HT或身份矛盾，拒绝整批计分。
- 明日179场完整前瞻池已冻结：5场有来源球队研究候选、174场阻断、0成对赛果、5场待结算。当前选择ridge=None，不自动启用新参数。
- 54场当前新历史在过去18个决策时点重放：每折可用训练数0、预测数0、Brier=null。证明时间门控工作，并非历史预测失误。

最新冻结截止：`2026-10-09T11:33:14.343182Z`，北京时间19:33:14。实际执行时间另行记录，未声称过去cutoff时已生成预测。

## 数学和选择流程

原有L1数值拟合不变：

`log(lambda_h)=log(mu_h)+attack[h]+defence[a]`

`log(lambda_a)=log(mu_a)+attack[a]+defence[h]`

以ridge惩罚Poisson似然，训练数据仅使用早于本折开球、且原件核验/结果可得时间均不晚于cutoff的记录；来源ID研究要求经核验的显式FT/HT。

对每个cutoff、每场可核验对阵，重新拟合全部预声明ridge。记此前已结算测试集合为E(t)，则：

`ridge_selected(t)=argmin_r mean_{m in E(t)} Brier(p_{m,r}, y_m)`

E不足30场选择None。None是同一提供训练档案上重新拟合的L3基线，不是借用竞彩v2.9的已公布偏差。测试标签不会进入自身训练或参数选择；晚到结果也不会提前进入下一折。

每个候选保留训练比赛ID、样本数、完整比分/六玩法向量、总均值变化。L1新均值来自球队效果估计，不能用“比分应更大”作为调整目标。v12的固定均值校准约束仍保留；L1相对基线的统计偏差另作下面的验证。

## 新增进球均值资格检查

单场误差 `e_m=E[G_m]-G_m_actual`，均值偏差 `bias=mean(e_m)`。

要求：

1. 同一成对测试集合中 `abs(bias_candidate)<=abs(bias_baseline)`；仅允许1e-9数值容差。
2. 至少5个已结算期块、500场成对样本。按期块有放回抽样2000次，`abs(bias_candidate)-abs(bias_baseline)` 的单侧95%上界不大于数值容差。
3. 候选均值偏差的双侧95%块bootstrap区间包含0。
4. 同时通过原有Brier、31比分logloss、无概率floor事件及完整输入足球池覆盖门槛。

这是上线前必要资格条件，不能证明数学意义的无偏；区间包含0也不能单独证明无偏。块数、样本量是预声明最低门槛，仍需功效评估，不是充分性保证。竞彩旧偏差-0.008没有自动迁移到北单新训练样本。

`research_qualification_gate` 汇总上述数值检查；`production_gate_passed` 始终false，来源/身份/完整官方测试池审计仍需独立批准。

## 真实前瞻冻结

`outputs/l1_prospective/20261009T113314682461Z/` 中保存：

- history.jsonl：54场真实、显式FT/HT的来源历史。
- folds.json：完整179场名单、cutoff、可用时刻及字段状态。
- results.jsonl：当前为空，不编造赛果。
- report.json：5场可运行的全部候选、174场阻断理由、0成对Brier、源码哈希和输入哈希。

官方让球来源缺少新的独立赛前核验，本次滚动测试 `official_handicap=null`，不会将旧档案线伪装成刚验证过的线。普通胜平负和比分仍可进行未来研究计分；完整六玩法资格尚未达到。

来源球队ID与比赛结构一致不等于中文北单绑定批准。5场候选全是来源ID研究；不能生产投放。v12的179场清单仍保留174场低信息先验；v13严格滚动账本将这174场记为阻断，不以低信息先验掩盖L1覆盖缺口。

## 复现

```bash
PYTHONPATH=. python -m unittest discover -s tests -p 'test_beidan_*.py'
PYTHONPATH=. python scripts/check_l1_historical_eligibility.py
```

可在仍未开球时创建新的前瞻冻结（会保存新的独立时刻，不覆盖原冻结）：

```bash
PYTHONPATH=. python scripts/freeze_l1_prospective.py
```

真实赛果取得并逐场来源绑定后，保存为新的结果文件，再计分原冻结cutoff：

```bash
PYTHONPATH=. python -m beidan_bd1.walk_forward \
 --history outputs/l1_prospective/20261009T113314682461Z/history.jsonl \
 --folds outputs/l1_prospective/20261009T113314682461Z/folds.json \
 --results VERIFIED_NEW_RESULTS.jsonl \
 --model-family l1_team_strength --identity-mode provider_native_espn \
 --out NEW_SETTLEMENT_REPORT.json
```

`VERIFIED_NEW_RESULTS.jsonl` 是未来实际取得并核验的结果，不是本包已有文件。不得把未来结果写回原预测时刻或修改旧冻结。来源ID结果字段、期号、kickoff必须与folds严格一致。

## 剩余真实缺口

174场缺足够来源球队身份/历史；中文对阵绑定和官方让球/SP的时间证据尚不足；目前尚无足够前瞻结算期、功效或实战Brier证据。执行器已经实现，不代表真实验证已经通过。外部仓库代码写入此前被自动审批拒绝，未改用Muse代写来绕过。
