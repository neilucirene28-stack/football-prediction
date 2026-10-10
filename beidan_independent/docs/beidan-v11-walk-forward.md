# BD-1 v11：逐期重拟合与过去折选参执行器

2026-10-09。此版本增加研究执行器和真实数据资格检查，**不代表模型通过生产验证**。默认在线runner没有切换到新候选；已有179场Excel和v10结果保留原版。

## v11实际执行什么

原 `replay_audit.py` 核验冻结预测的训练血缘并计分，不会训练。新增 `walk_forward.py` 在每个明确的 cutoff 上重新筛选历史、拟合独立L3根先验，并重新生成候选。真实运行时间单独写入 executed_at；不会把今天重跑的时间倒填成历史 generated_at。

这是当前可复跑的 **L3校准研究路径**，不是球队强度L1的完成声明，也不使用尚未核验的市场SP。L1、赛事族和市场特征的正式滚动训练尚须对应的可审计历史输入。这里只对有明确来源时间的完整足球测试池评估。

每一期的流程：

1. 验证完整输入池长度、期号、唯一比赛ID与严格递增cutoff；cutoff必须早于该期所有比赛开球，并且不晚于计分时点。
2. 仅使用 kickoff<cutoff、赛果实际可得/首次核验时间≤cutoff 的常规时间历史。赛果可得时间和核验时间同时存在取较晚者；历史与计分标签的同一ID矛盾时拒绝。同一ID在两路有不同已记录时间时保守取较晚者。
3. 根先验从当时可得比分重新拟合，不缓存当前1665场结果去预测过去。半场信息缺失不伪造；没有足够训练历史就记 blocked。
4. 对每个候选参数使用同一训练窗口。先验无让球三向概率p，训练窗口实际三向计数nᵣ，候选目标为 qᵣ=(nᵣ+κpᵣ)/(n+κ)。κ网格为 identity、25、50、100。identity完全保持先验。不会硬套29%平局率。
5. 非identity候选使用v10固定总进球均值的KL投影；不可行时整场保留先验并记录fallback，不删除难预测场次。
6. 选择κ时，只看**更早测试折**且赛果在当前cutoff已可得的成对Brier；不足30场时用identity。当前测试期的输赢不能参与本期选参。网格是代码中声明的研究候选，不声称在旧历史测试期前已有真实预注册。
7. 从同一比分矩阵输出六玩法；官方整数让球线需要独立handicap_source，缺线则让球概率为null，半球线明确拒绝。无让球校准目标不会被当成带线市场目标。
8. 已结算并在evaluated_at可确认的同一场次成对计分；未结算者保存pending而不产生假Brier。所有跳过保留在账本，足球和非足球分别统计。

训练history须包含各次新结算的完整常规时间全场/半场标签及真实时间；results用于本次计分和过去折选参。不能只给全场比分就自动伪造半场并加入重新训练。新赛果需追加到规范化history，下一折才可在其真实可得时间之后消费。

## 计分和研究数值门槛

Brier明确采用三项平方误差的平均 `sum((p-y)^2)/3`，与未除3口径相差固定3倍。不同口径不能直接比较历史0.622等数字。

比分使用北单31类概率的log loss；超出列举比分的赛果归入胜其他/平其他/负其他。对小于1e-15的概率设计算下限，并单独计数；出现这种下限事件时数值门槛不通过，避免下限掩盖坏预测。

研究门槛要求：至少5个已结算测试期、500场成对样本，完整足球池的预测和计分覆盖，总体ΔBrier≤0、按期成块抽样2000次的单侧95%上界≤0，同时31类比分log loss不恶化、没有log loss下限事件。少于5个已结算期不输出退化的置信区间。以比赛数加权；同场baseline/candidate共同进入或共同记阻断。阈值是当前实施设计，并非统计功效已达成的证明。

即使研究数值门槛通过，`production_gate_passed`也保持false：还必须核验官方池完整性、原始档案与时间证据、参数/数据版本隔离、赛事族校准和样本功效。身份与市场的其他实验不能借本门槛获得放行。

## 测试与当前真实数据检查

独立北单测试共66项通过。新增8项检查覆盖实际逐期重拟合、过去折选参、当前未来赛果不能改变当前预测、迟到标签不参与训练/选参、未知来源和非足球保留账本、未结算不计假Brier、重复/不完整/矛盾输入拒绝、整数线来源与正负线传播，以及分期bootstrap的样本/覆盖/比分门槛。测试中的小样本选参阈值为1，仅用于功能验证；默认阈值仍为30。

当前1665场的可得/核验时间为今日批量verified_at。为旧8期各取首场开球前一小时做**资格示例**，可使用的历史均为0；这些cutoff不是对旧部署时刻的声称。今天以后可以用1665场拟合先验，但不可以回填成旧赛前可得数据。

现有179場WDL表格能核对比赛字段和整数线，但原提供商抓取时刻仍未知。资格示例保持fixture_source.available_at=null，当前执行器记179场blocked、Brier=null。此前179场观察预测仍保留，不因这个研究门控被删除或变为生产预测。

这一结果定位了数据门槛，不能解释成模型效果差，也不能把66项软件测试通过解释成真实比赛验证通过。新增数据审计见下节；任何来源采集时间不能由摘要本身认证。

## 输入格式和CLI

规范化history每行至少有 match_id、带时区kickoff_at、regular_time=true、ft_home/ft_away/ht_home/ht_away，以及真实result_available_at或verified_at。计分results每行须有period、match_id、kickoff_at、regular_time=true、全场比分和真实赛果可得/核验时间。当前未结算则不要伪造赛果行。

folds为JSON数组，每项字段：period、cutoff_at、expected_total、fixtures。逐场至少有period、match_id、sport、kickoff_at、fixture_source={name,status,available_at}；真实可用来源status=ok。已核验整数线可附official_handicap及handicap_source。上述状态和时间声明仍须与原始收据和来源文件核验，程序不认证第三方身份。

```sh
PYTHONPATH=. python -m unittest discover -s tests -p 'test_beidan_*.py'
PYTHONPATH=. python scripts/check_current_walk_forward.py
PYTHONPATH=. python -m beidan_bd1.walk_forward \
  --history history_normalized.jsonl --history-format normalized \
  --folds folds.json --results results.jsonl --out new-report.json
```

第二条会用真实当前UTC制作一次不可覆盖的资格报告及CLI输入；须在这批比赛全部开球前执行。随包的 input_* 目录可直接用于第三条，预期仍阻断179场；它是诚实的缺来源示例，不是假通过样例。`--history-format legacy1665`仅用于既有八期原始导出协议；未来新赛果使用normalized格式。

CLI输出独占创建，不覆盖已冻结结果。所有代码与输入同包保存，摘要可验证字节一致性；不会宣称摘要证明提供商发布时间或不可篡改性。仓库写入此前被自动审批拒绝，交付包是可审阅本地成果，尚非已同步或上线代码。

## 本轮原始数据核验

872d0ce的269条加工近况对应257个唯一provider/event，重复12条；其中1条明确为波兰冰球，另有林茨蓝白误绑LASK。29条TSDB只有日期，全部269条缺半场，三个raw仍是加工对象，已隔离且未导入训练。

9b4f6b8的四队真实ESPN schedule共112次观察，混合赛季阶段且包含19次点球决胜观察。`espn_schedule.py`仅保留2026年type14287常规赛的90分钟FT：32次观察去重后30场，四支目标队各8场。不同阶段与点球/加时记录不混入。

eeb44fc进一步提供这30场真正summary原件及逐请求收据。`audit_espn_summaries.py`已核验30/30字节大小与收据摘要前缀一致，30/30比赛ID、阶段、时间、主客及FT与schedule一致；30/30具有显式两半场linescores，和完整进球事件逐半场计数一致。完整SHA256保存在本地审计报告。核验时刻为2026-10-09T10:01:43Z（北京时间18:01:43），不能倒填为今日18:00前的预测输入。

`espn_summary.py`只读取显式linescores，不从总比分均分，不由不完整进球事件推半场；摘要对应原始字节，矛盾时拒绝。记录仍是provider身份，中文球队与北单fixture的规范映射尚未批准，因此 identity_verified=false、model_imported_n=0、production_eligible=false。收据宣称的09:58:33–09:59:41Z提供商捕获时钟未被独立认证；verified_at保留Codex实际核验时刻，旧赛前首次可得时间保持null。

新增4项schedule测试和4项summary测试核验阶段/点球隔离、来源ID去重、显式半场、比分/身份矛盾和缺半场不推断。真实30份原件的批量审计与软件66项测试是两类证据，均不能替代Brier上线验证。

复跑新增数据审计：

```sh
PYTHONPATH=. python scripts/audit_new_form_data.py
PYTHONPATH=. python -m beidan_bd1.espn_schedule
PYTHONPATH=. python scripts/audit_espn_summaries.py
```

当前剩余P0：179场新鲜赛程/SP的真实收据和完整原件、跨源fixture/球队/赛事规范身份、更多对应联赛的合格训练历史，以及后续已结算成对walk-forward结果。旧1665场只在今日核验后可消费；无历史北单payload不能用重跑结果冒充历史生产回测。Muse仅补数据，模型开发与验证由Codex负责。

## 欧洲赛程补查

f53bc2f的欧洲10队真正schedule，128次观察、124个唯一事件，全部FT；10个原始文件大小和摘要前缀已核验，provider team.id与请求队ID一致。这些响应本身没有未来fixture，不能把已结束比赛改日期充当未来赛程。

随后bb0c436使用四联赛scoreboard，分别查询UTC20261009/20261010的8份真正响应。`audit_espn_fixtures.py`独立核验原件摘要、足球事件空间、唯一赛事ID、提供商主客ID与方向、比赛赛前状态、timeValid、两级UTC时间及北单旧池时间，seq18/19/22/23/24共5场结构一致。北京时间10-10凌晨须查UTC10-09，球队近况schedule不是未来每日赛程接口。

这些是提出的跨源绑定，仍未批准canonical身份。提供商赔率/半球线与官方北单WDL整数线分开，预开球score=0只是占位值，不作赛果。不能因为ESPN原件含外部庄家odds，就把其pointSpread作为北单整数线或把open/close标签当有时序证据的历史收盘快照。新增3项fixture测试覆盖结构一致且不批准身份、主客/时间/运动矛盾拒绝、错误运动事件空间与进行中状态拒绝。

```sh
PYTHONPATH=. python scripts/audit_espn_fixtures.py
```

Muse正在用已有支持映射扩展明日179场覆盖账本；未交付/未核验记录保留缺失，不把5场说成179场已完整新采集。

## 明日179场覆盖账本独立审计

7c1799b含179行，期号/seq11–189/主客/联赛/UTC时间均与原池一致；原报告matched_prior=5，unmatched=174。MANIFEST实际77项：60个HTTP200原件、16个HTTP400请求、1个未映射项；并非82份成功原件。60份原件大小/摘要前缀/足球事件空间等核验通过，共137个唯一ESPN事件。

`scripts/audit_179_coverage.py`进一步限制来源联赛代码及赛前状态，再比较相同UTC开球；101场有至少一个候选，78场没有ESPN候选。26场恰好一个候选也不自动批准名称/身份；其余存在同时间多个候选。中文→代码来自既有collector硬编码，尚未外部批准，故上述仍为“在提出的联赛映射下有时间候选”，不能称为101场已匹配或覆盖成功；赛季阶段也留在每个候选中等待与目标赛事证明核对。

140a586纠正了60份成功原件的数量，但AF所谓1495条raw实际仍是加工数组（fixture_id/date/home/away等），没有API顶层响应结构，也没有start/end/HTTP收据。旧文件的本批次mtime不作采集证据。该文件隔离归档，不导入预测或训练。已要求Muse仅补一次真正原始fixtures响应，不能包装加工数组冒充provider body。

```sh
PYTHONPATH=. python scripts/audit_179_coverage.py
```

本轮软件66项测试通过。现有179场观察结果、固定均值716条数值比较和来源不足的179场资格结果均保留不同标签，不合并成“179场已完成生产预测”。

## 最终补齐的AF真正响应

8ee2007交付API-Football真正fixtures响应与无key收据，参数date=2026-10-10、timezone=Asia/Shanghai；实际声明请求时间10:16:05–10:16:08Z，Codex核验时间10:19:39Z。原件1,431,119字节，完整SHA256 bb1ce501e5078854e25d17a4ad1b5fd6cbe3d456e35e70c6c25b926280252ed3与收据一致。包含get/parameters/errors/results/paging/response，errors为空，单页1450条唯一fixture。

`audit_af_response.py`核验日期/时区、epoch与带时区date一致、唯一fixture和主客ID、状态、空比分、完整参数与分页。1450条中1439未开球、3取消、8延期；346个来源联赛/赛季。未来goals及各段比分均null，不能当历史标签，也不能用未开球score占位或猜测半场。原1495条加工数组保持隔离，不能与这1450条混用或称为同一次响应。

该原件已补齐真实结构与收据，但提供商捕获时钟仍是采集方声明；按Codex当前verified_at保守使用。北单中文fixture尚未绑定到这些provider ID，取消/延期也不能仅凭同开球时间套到北单行。模型导入=0、生产=false；下一阶段应利用本原件的稳定provider ID和联赛目录补身份，再采集相应可审计的历史，而不是继续生成无法核验的加工导出。

```sh
PYTHONPATH=. python scripts/audit_af_response.py
```
