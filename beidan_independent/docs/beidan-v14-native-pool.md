# BD-1 v14：跨联赛原件核验与完整池预测

## 本轮实际交付

- 英甲来源提交aa9fff60a3918c12388876ffc5e9a3862cbed5f2：24个schedule、90个summary，114/114原始字节数与完整SHA256匹配。
- 同赛季2026、阶段14336：180个球队赛程观察去重为90场90分钟FT；90/90 summary与赛程ID、联赛、主客、开球、FT一致，90/90显式HT，无缺失或拒收。
- Codex本轮实际核验时刻2026-10-09T12:25:44.828975Z（北京时间20:25:44）。该时间供未来预测使用，不倒填历史可得时刻。收据的提供商采集时钟未获独立认证。
- 研究球队模型：原5场德乙加12场英甲，共17场；179池保留其余162场低信息根先验，不冒充逐队分析。完整输出六类概率向量、比分31类、Top5。
- 英甲绑定为Codex提出的中文到来源ID候选：98、99、138–147，严格核对来源主客ID、赛事与UTC；不表示已获规范身份人工批准。
- 88项测试通过。新冻结179场赛前快照：20261009T122629673129Z，17场研究候选、162场阻断、已结算配对0、Brier=null。

## 实现

`scripts/audit_native_batch.py`接受显式联赛/赛季/阶段配置，校验每份原件大小、SHA256、收据顺序、HTTP，然后严格解析赛程、去重、对照summary的显式半场。没有HT不推算；不把加时或点球结果混入90分钟。

`beidan_bd1/native_pool.py`合并已核验批次，拒绝迟于cutoff的数据、矛盾历史、混合阶段、重复seq以及同一provider事件绑定多个seq。每个未来fixture保持本联赛/赛季/阶段元数据；不跨联赛混用球队ID。

球队模型沿用v13的带ridge正则Poisson攻防强度拟合；研究输出预声明ridge=5，不代表已通过验证的参数。数学链路与固定均值校准不变，不为显示大比分增加λ。

冻结验证保留ridge=None/2/5/10/20候选；无至少30个过去可得结算标签时仍选择None基线。因此研究表的ridge=5预测，与当前walk-forward选出的冷启动基线是不同对象，报告中分别记录，不能互换性能结果。

## 复跑

在独立目录执行：

```sh
PYTHONPATH=. python -m unittest discover -s tests -p 'test_beidan_*.py'
PYTHONPATH=. python scripts/audit_native_batch.py data_sample/espn_eng3/AUDIT_CONFIG.json
PYTHONPATH=. python scripts/emit_native_pool.py outputs/ger2_native/audit_and_predictions.json outputs/eng3_native/audit_and_predictions.json --output outputs/new_research_run
PYTHONPATH=. python scripts/freeze_native_pool.py outputs/ger2_native/audit_and_predictions.json outputs/eng3_native/audit_and_predictions.json
```

核验复跑会产生新的真实核验时间。冻结器每次创建新目录，不覆盖已冻结预测；不要在开球后冒充赛前快照。

## 验证与剩余硬缺口

历史原件只证明结构与比分正确，不证明模型准确性提升。当前不能据今日归档重构过去赛前payload；不得伪造历史walk-forward成绩。

生产仍需至少5个已结算时间块、500个完整配对样本；Brier配对差及按期bootstrap置信上界均不恶化，比分log loss不恶化，原总进球偏差与置信区间满足v13均值门槛。完整池覆盖与身份、可得时间审计为额外门槛。

新鲜官方北单整数线与SP证据仍缺；预测表旧线仅供注明来源的研究推导，冻结walk-forward让球字段保持null。SP未进入本轮模型。

162场仍缺能进入来源ID球队模型的已核验历史或fixture绑定；全部保留在池中。英甲与德乙历史为已观察赛程并集，尚未独立证明完整赛季无截断。所有production_eligible=false，不能合并部署。

## 仓库状态

v13已在codex/beidan-bd1-v13-import分支，草稿PR #2存在。v14补丁只更新独立目录入口并新增代码、数据、报告和冻结快照，不修改共享engine与生产配置；本次仓库写入需独立核验提交后的文件摘要。
