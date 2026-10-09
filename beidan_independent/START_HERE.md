# BD-1 v11 可复跑交付

模型开发、参数与验证由Codex负责；Muse只提供数据。所有新路径维持研究/影子状态，production=false。本包不表示已写入GitHub或上线。

## 当前已完成

- 独立比分矩阵与六玩法完整向量、整数主队让球事件空间、缺失降级与全池账本。
- 固定总进球均值的KL投影。179场四个预声明权重共716条结果保存在outputs/fixed_mean_20261010；这不是选出最佳权重的回测。
- 真正逐期重拟合L3，候选只由过去已可得测试折选择。Brier采用三项平方误差平均，配对比分31类log loss，按期bootstrap；至少5期500场。未结算不计假Brier。
- 66项独立北单测试通过；真实30场ESPN显式半场与完整进球事件交叉核验通过。

## 运行

Python 3.11+，标准库即可；解压后在此目录执行：

```sh
PYTHONPATH=. python -m unittest discover -s tests -p 'test_beidan_*.py'
PYTHONPATH=. python scripts/audit_new_form_data.py
PYTHONPATH=. python -m beidan_bd1.espn_schedule
PYTHONPATH=. python scripts/audit_espn_summaries.py
PYTHONPATH=. python scripts/audit_espn_fixtures.py
PYTHONPATH=. python scripts/audit_179_coverage.py
PYTHONPATH=. python scripts/audit_af_response.py
```

随包的真实当前资格示例保持179场来源时间缺失，因此预期179场blocked、Brier=null。不是故障或假成功样例。指定新输出路径复跑：

```sh
PYTHONPATH=. python -m beidan_bd1.walk_forward \
  --history outputs/walk_forward_qualification/input_20261009T093916834013Z/history_normalized.jsonl \
  --history-format normalized \
  --folds outputs/walk_forward_qualification/input_20261009T093916834013Z/folds.json \
  --results outputs/walk_forward_qualification/input_20261009T093916834013Z/results_pending.jsonl \
  --out rerun-new-path.json
```

报告独占创建，不能覆盖原结果。以下两条仅能在179场全部开球前运行，因为真实运行时间必须保留：

```sh
PYTHONPATH=. python scripts/check_current_walk_forward.py
PYTHONPATH=. python scripts/audit_179_fixed_mean.py
```

## 尚未达到的条件

| 项目 | 当前事实 | 下一步 |
|---|---|---|
| 明日179场原始池 | 179整数线、176SP，缺140/143/169；提供商首次抓取时间未知 | 补新鲜原始响应及实际收据，缺项保留null |
| 球队/赛事与fixture身份 | provider ID不是已批准的跨源规范ID | 从真实赛程绑定双方、赛事、开球，留审核证据 |
| 新近况数据 | 269条加工近况已隔离；30场J1原件FT/HT结构核验通过；新增AF真正1450条未来fixture通过结构与摘要核验 | 未批准身份、未导入L1；不得回填为旧赛前输入 |
| 历史验证 | 1665场今日才批量核验，无旧赛前payload；旧折可得历史为0 | 消费真实新增历史及冻结未来预测，等待结算配对验证 |
| 新候选效果 | 软件与数值检查通过，不等于Brier不恶化 | 真实walk-forward与覆盖、比分、校准审核通过后再上线 |
| 仓库同步 | 写入已获用户授权，但自动审批拒绝了create_tree | 当前交付可审阅包；没有伪称已推送 |

完整算法与实施设计见docs/beidan-independent-model-v1-design.md；v11协议及本轮数据审计见docs/beidan-v11-walk-forward.md。原179场观察预测位于outputs/20261010_179_shadow.json，保持原版；其旧无约束重加权曾改变均值，不能作为固定均值新实现的证据。

MANIFEST.json仅证明本包字节与记录摘要一致，不认证提供商采集时钟或历史赛前可得性。

## v12 当前入口

优先阅读 `docs/beidan-v12-main-chain-native-history.md`。当前74项独立测试通过；主校准入口已固定总均值。新输出为 `outputs/20261010_v12_research/179_probability_vectors.json` 和同目录 `179研究清单.md`：5场来源球队强度研究候选，174场低信息根先验。全部未上线、未批准中文绑定，旧市场重加权输出仅保留诊断。原件首次可用时间不得倒填。

## v13 当前验证入口

新增 `docs/beidan-v13-l1-walk-forward.md`。80项独立测试通过；球队强度每折重拟合、历史已结算参数选择、进球均值偏差资格门槛已经实现。最新前瞻冻结为 `outputs/l1_prospective/20261009T113314682461Z/`：179场完整账本，5场来源球队候选、174场阻断，当前无赛果/Brier。v12的179场研究输出仍保留作参考，不能误解为完整L1覆盖。
