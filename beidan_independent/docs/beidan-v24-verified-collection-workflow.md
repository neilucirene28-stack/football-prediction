# 北单 v24：原件核验合并与最早封存采集队列

仅续接北单分支 `codex/beidan-bd1-v13-import` / 草稿 PR #2，上传基点 `09607545cd73438fbf9cb1ee113e9436eca4e5ef` 已与远端核对。此次改动是结算流程迭代，没有重新训练、重新选参或改写任何赛前封存，不声称提高模型准确率。

## 修复的流程缺口

v23 已逐场保存、核验19份HTTP原件，但合并步骤依赖一次性手工操作。新增 `beidan_bd1.collection_workflow` 将它变成可复现的入口：

1. 先校验已保留的注册表摘要，并从全部原封存重建最早预测归属。
2. 校验每个采集索引的已保留摘要、原封存、完整池账本及采集范围。
3. 对每份响应核验URL中的赛事slug和event ID、原字节长度及SHA256、接收及核验时钟顺序。支持已经拆分的无损原件。
4. 使用原核验时间与原冻结池重新导入HTTP正文；新导入记录必须与已保存赛果完全一致。非200、缺失、冲突或改动的证据不能变为已核验赛果。
5. 同一比赛重复、相同赛果只保留最早核验记录，单独列出被排除观察；FT/HT或来源身份冲突时中止合并，不能静默覆盖。输入原件和原预测始终不变。
6. 每场按最早合格原概率结算，输出179行完整账本。待采集按最早封存分组，跨期相同场号不会合并成一场。

摘要只能检测相对已保留摘要的内容变化，不能独立证明来源、时钟或北单官方身份。正式结算依然使用既定L3冷先验，L1 ridge=5只作为预先声明的诊断。

## 实际复现结果

输出目录：`outputs/v24_verified_collection_workflow_20261010/`。

| 项目 | 当前结果 |
|---|---:|
| 完整目标池 | 179 |
| 唯一原赛前预测 | 66 |
| 未覆盖 | 113 |
| 原件核验并成对结算 | 19 |
| 待赛果 | 47 |
| 已到采集时间 | 0（本次执行时） |
| 正式 Brier，三类别平方误差平均 | 0.20622960801176707 |
| 正式方向命中 | 10/19 |
| L1 ridge=5诊断方向命中 | 12/19 |
| L1 ridge=5诊断 Brier | 0.18632204837403651 |

新合并 `results.jsonl` 与 v23 唯一赛果文件逐字节一致，19场既有结论不变。原始接收时间、核验时间和预测概率均保留。本次执行实际时间见 `MERGE_AUDIT.json`，代码摘要也保存在该文件中。

剩余47场在本次执行时都还未开赛，最早一场的原冻结开球时间为2026-10-10北京时间13:00。采集延迟105分钟后，最早14:45（06:45 UTC）可以发起完赛检查；仍必须由原件显式常规时间FT状态确认，不能把等待时长当作完赛。

## 可重复运行

在 `beidan_independent` 下执行。输出必须指定一个不存在的新目录，不覆盖已保存执行：

```bash
PYTHONPATH=. python -m beidan_bd1.collection_workflow \
 --registry outputs/v22_prospective_registry.json \
 --registry-sha256 3b45be0123dfa0156dc48ae24907b6aaa594e2030dac60f3591f3c64b14176b3 \
 --collection outputs/v23_result_collection_v19_20261010_morning 821c9868d48a1cd2c8a88a0ca681b228cfd6b344225ce61174cbe556c4ab92e0 \
 --collection outputs/v23_result_collection_v22_ned2_20261010_morning f655c90a61f72bddbe183ab5161598d33e7eff8467037b0b622eb53bf1bcc9aa \
 --out /tmp/beidan_v24_new_execution
```

更新清单时重新执行该命令，使用真实执行时钟。`pending_queue.json` 是该次执行的状态快照；`collection_groups` 只包含当时已到检查时间、仍未结算的最早预测。它不自动调度或请求网络。

ChatGPT 后续采集时，对每组调用已有采集器：

```bash
PYTHONPATH=. python -m beidan_bd1.result_collection \
 --bundle ORIGINAL_BUNDLE --manifest-sha256 ORIGINAL_MANIFEST_SHA256 \
 --match-id FIRST_DUE_ID --match-id SECOND_DUE_ID \
 --out NEW_EXCLUSIVE_COLLECTION_DIRECTORY
```

将新采集索引摘要保留后，通过新增 `--collection NEW_DIRECTORY INDEX_SHA256` 将它与全部已有采集合并。请求失败、未完赛、半场缺失须保留真实状态；不从截图或另一玩法拷贝赛果。原采集失败不得删除以伪造顺利采集。

## 校验与资格

北单独立测试共174项，包含原件改动、伪造赛果、索引改动、错误URL、失败HTTP、倒置时钟、完整池丢行、重复响应、重复赛果、冲突赛果、无损原件以及跨期同场号。测试中的改动/合成记录不计入真实模型成绩。

```bash
PYTHONPATH=. python -m unittest discover -s tests -q
```

当前仍为1期19场，未满足5期/500唯一成对赛果门槛。625场已核验FT/HT历史是训练来源，不是625场独立前瞻模型成绩。中文身份绑定未批准、官方让球/SP缺失，不能计算北单官方让球命中或ROI。生产资格保持false。

Muse只上传已完成改动及更新PR元数据；后续开发、来源采集、校准回测由ChatGPT继续。当前不合并或部署草稿PR。
