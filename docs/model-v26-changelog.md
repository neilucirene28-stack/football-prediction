# 模型 v2.6 更新日志（2026-10-07）

依据：任务二已验证的结论（让球 overconfidence 诊断 n=418；venue 编码 bug）。
铁律执行：单测 + walk-forward 证明真提升才上，否则诚实否决。

---

## 1. venue 编码 bug 修复 ✅ 已合并

**问题**：`scripts/backfill_phase2.py::to_recent` 输出小写 venue `"home"`/`"away"`，
而 `engine/strengths.attack_defense` 只认 `"H"`/`"A"`/`"N"`。
后果：225 场 Phase2 回填的近况被 venue 过滤条件静默丢弃（攻防评级退化为 1.0，
即用联赛均值代替球队真实近况）。生产链路（daily_fetch/collector）不受影响，
sep2026 回填（已用 `"H"`/`"A"`）不受影响。

**修复**（最小改动，只动源头）：
- `to_recent` 输出 `"venue": "H" if is_home else "A"`（与 engine 约定一致）。
- 策略（选其一并写明）：在源头做转换，不改 engine 的严格过滤语义。
  engine 层对未知 venue 值保持静默过滤（历史行为，不扩大改动面）。

**回归单测**（tests/test_backfill_phase2.py，3 项通过）：
1. 输出 venue 恒为 `"H"`/`"A"`，显式断言小写不复发；
2. 输出能被 `attack_defense(venue="H"/"A")` 实际消费（n>0）——这正是 bug 的核心：
   旧输出下该 n 恒为 0；且有真实近况时评级不再退化为 (1.0, 1.0)；
3. gf/ga 按被统计球队视角定向（主队视角 venue=home 的场 gf=hg）。

---

## 2. 让球 1X2 温度缩放尝试 ❌ 已否决（不合并）

**动机**：任务二确认让球胜平负系统性 overconfidence（S 形，随置信升高加剧，
全引擎最大的校准短板），而 v2.5 的 letdraw 让平校准本身是好的。
候选修法：单参数温度缩放（最小改动），与 letdraw 正交组合。

**验证设计**（/tmp/handicap_temp_cv.py、handicap_temp_cv2.py，脚本保留在 /tmp 备查）：
- 样本：三 backfill jsonl 中已结算且 jc_rq 非空非 0 的 418 场（2026-09-01~30），
  重建口径与 analyze_phase2.py 完全一致：
  `raw = handicap_1x2(score_matrix(lh, la, rho=-0.13), int(jc_rq))`，
  实际 = 让胜/让平/让负（adj margin 口径）；lam/让球/赛果均为赛前可得或赛果字段，无泄漏。
- 基线 = 生产现状：`calibrate_handicap_1x2(raw, rq, league, strength=0.5)`。
- 候选 A：`temper(raw, T)` → letdraw(0.5)（温度修 H/A 过度自信，让平仍由 letdraw 管）；
  候选 B：letdraw(0.5) → `temper`（备选顺序）。
- 5 折按时间顺序分块 CV：T 只在训练折上拟合（训练折 NLL 最小，T∈[1,10] 只做降温），
  在测试折评估；判定标准 = 配对 bootstrap（5000 次）95% CI 的 mean(ΔBrier) 完全 <0。

**基线诊断复现**（确认任务二结论）：基线 Brier=0.6948；top∈[0.6,0.7) 预测 0.635 vs
实际 0.465（gap +0.170）；[0.7,0.8) 预测 0.758 vs 实际 0.429（gap +0.329）；
[0.8,0.9) 预测 0.840 vs 实际 0.364（gap +0.476）。overconfidence 属实。

**初看"通过"的结果（陷阱）**：全样本 5 折 CV，候选 A Brier 0.6948→0.6478，
mean(ΔBrier)=-0.047，95%CI=[-0.0745,-0.0173]，拟合 T≈2.8~5.2；Brier 分解
REL 0.0619→0.0102（校准项大幅改善），首选 0/418 翻转（温度单调，符合预期），
让平残余由 +0.033 变为 -0.025（仍小，与 letdraw 不打架）。

**分源拆解后否决**（关键证据）：
- jingcai 干净样本（n=194，输入无 bug）：5 折 CV mean(ΔBrier)=-0.0076，
  95%CI=[-0.0348,+0.01899]——**包含 0，不显著，无 measurable 提升**。
- phase2 样本（n=224，即本日志第 1 节修复的 venue bug 污染样本，近况被静默丢弃）：
  mean(ΔBrier)=-0.0932，95%CI=[-0.1392,-0.0445]，"显著"，且拟合 T 顶到搜索上界 10.0
  （模型试图把预测直接抹平成均匀分布）——这是**对垃圾输入的补偿**，不是真正的校准修复。

**否决理由**：显著性完全由 bug 污染子样本驱动；在干净的生产代表性样本上
无证据（n=194，CI 过零）。且该 bug 已在本日志第 1 节修复、生产链路从未受影响，
把温度缩放并入生产等于给一个已不存在的数据 artifact 做拟合——违反"有证据才上"铁律。
**结论：不合并 engine 改动**，本次仅修 bug + 加回归测试。

**后续**：待 venue 修复后的新回填数据攒够干净让球样本（n≥300）后，可重跑本验证脚本；
若干净样本上 ΔBrier 显著为负再议。

---

## 3. 明确不做的（沿用任务二结论，不重复验证）

- 弱赛事 shrink 6.0 vs 3.0：无 measurable 差异且无伤害证据——不动。
- 胜平负 [0.5,0.7) underconfidence：REL 本身很好（0.0042）——不动。
- "模型比市场更自信同向"假设：n=22, z=-1.38 未达显著——证据不足，不动。

---

## 附：测试状态（2026-10-07）

- 全量：178 passed, 1 skipped, 2 failed——2 个失败为 **pre-existing**（干净树同
  样失败）：tests/test_beidan.py::TestPredictModelParam 的两个用例因硬编码开球
  日期已过期（predictor 拒绝赛后预测，属时间炸弹测试），与本次改动无关。
- 新增 tests/test_backfill_phase2.py：3 passed。
