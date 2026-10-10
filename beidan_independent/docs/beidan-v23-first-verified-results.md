# 北单 v23：首批 19 场真实原概率结算

北京时间 2026-10-10 上午，原 v19 冻结采集 10 场有效赛果，新 v22 冻结定向采集荷乙 9 场。19 次公开 ESPN summary HTTP 请求均成功，逐份保留原始响应、实际收到与核验时刻、字节数和 SHA256。9 场荷乙于 02:00 开球，原 v22 实际封存于 00:40:57；本轮只收赛果，没有赛后补写赛前预测。

## 采集范围与完整分母

`result_collection.collect` 新增可选 `only_match_ids`，CLI 可重复传 `--match-id`。范围必须是非空、无重复的原冻结预测 ID；未知、原阻断或字符串伪列表在请求前拒绝。范围之外的预测标为 `outside_collection_scope`，原阻断仍标 `originally_blocked`，完整 179 场采集账本不缩水。默认不传范围时保留旧行为。

执行代码摘要改为在 HTTP 请求前取得，避免结束时文件内容代替采集开始时的版本。荷乙请求期间未更改采集器文件。

两份独占采集目录：

- `outputs/v23_result_collection_v19_20261010_morning/`：10 场。
- `outputs/v23_result_collection_v22_ned2_20261010_morning/`：9 场；明确记录 9 个请求 ID，保留 179 行完整账本。

合并前重新验证全部 19 份原件的字节数、摘要、来源 URL、原冻结 event ID/league slug/阶段及时间顺序；以原 `verified_at` 重导入，要求完整记录一致。原审计及原验证时刻不改。合并输入和检查记录：

- `outputs/v23_unique_verified_results_20261010_morning.jsonl`
- `outputs/v23_unique_result_merge_audit.json`

不累加多版本结算报告，不覆盖旧原件，不重训或赛后重选。

## 唯一赛事结果

使用 v22 最早合格封存登记表，独立保留 SHA256：
`3b45be0123dfa0156dc48ae24907b6aaa594e2030dac60f3591f3c64b14176b3`。

完整池 179 场；66 场唯一预测、113 场未覆盖。19 场结算，47 场已预测待赛果；排除 162 条后续重复预测。

正式已选与基线 Brier 均为 **0.20622960801176707**，差为 0；31 类比分 log loss 均为 **2.8232913195810205**。Brier 是三类别平方误差的平均，再按赛事平均。

正式所选均为 L3 冷启动先验。命中最高概率方向 10/19；事先声明的 L1 ridge=5 诊断方向 12/19、Top3 比分 6/19、诊断 Brier=0.18632204837403651。这是诊断描述，不能用这批赛果把原来已选的 L3 改成 L1。

正式进球均值误差（预测减实际）+0.8292072981775761 球；L1 诊断 +0.9908657485441031 球。该小样本没有证明均值无偏，不能只因众数比分低而调高进球均值。荷乙诊断主胜约 81.1% 的阿尔青年队实际 0-1，保留此失败记录。

原 v19 单版 10 场 Brier=0.21115812402553172；用最早封存去重后的首批 10 场 Brier=0.21392148912740638。这两种口径不混写；原两份报告都保留。

主结算：`outputs/v23_unique_settlement_19_verified_results.json`。
逐场复盘：`outputs/review_20261010_v23.md`、`outputs/v23_first_19_review.json`。

## 复现

```sh
PYTHONPATH=. python -m beidan_bd1.unique_settlement \
 --registry outputs/v22_prospective_registry.json \
 --registry-sha256 3b45be0123dfa0156dc48ae24907b6aaa594e2030dac60f3591f3c64b14176b3 \
 --results outputs/v23_unique_verified_results_20261010_morning.jsonl \
 --out /tmp/v23_unique_check.json

PYTHONPATH=. python scripts/review_frozen_results.py \
 --registry outputs/v22_prospective_registry.json \
 --registry-sha256 3b45be0123dfa0156dc48ae24907b6aaa594e2030dac60f3591f3c64b14176b3 \
 --results outputs/v23_unique_verified_results_20261010_morning.jsonl \
 --json-out /tmp/v23_review_check.json \
 --markdown-out /tmp/v23_review_check.md
```

复盘脚本先调用唯一结算验证最早封存归属及重复赛果，再读取原模型族和预声明 ridge=5.0；生成的正式 Brier 必须与已验证唯一结算一致。输出采用独占写入。重新运行会有新的实际执行时刻，原概率和评分不变。

## 验证与分工

159 项 unittest 通过。新增采集测试验证定向请求不丢全池分母，空/重复/未知范围拒绝，以及原阻断不得指定采集。实际合并核验 19 份原始响应，荷乙实际结算 9 场；结果仅用于原概率评价。

当前 1 期/19 场，低于至少 5 期/500 唯一成对赛果的门槛，生产标志仍 false。中文身份绑定全部未批准、官方让球/SP缺失，不计算北单让球命中或 ROI。其余联赛历史缺口仍在，不能声称完成全池预测。

用户已明确：Muse 只负责上传准备好的附件；开发、后续赛果采集和模型复盘由 ChatGPT 继续。本地 v22/v23 成果待实际远端提交验证；草稿 PR #2 不合并或部署。
