# 北单 v20：跨版本唯一赛事结算

v19冻结四版合计164条研究预测，实际只有57场唯一赛事。直接相加多个冻结版或重复执行的结算报告，会重复计分。本次补齐`beidan_bd1.unique_settlement`，按独立摘要核验登记表，再从各原冻结清单重新构造最早合格版本；不信任登记表的行顺序或后期修改的版本选择。相同期号/比赛的来源身份冲突直接拒绝，同一ESPN event_id更换期号或阶段也不能被当作另一场。

## 实际执行结果

当前179完整池、57唯一研究预测、122阻断、0成对赛果；排除107条后续版本。北京时间2026-10-10 00:02重新运行采集器，全部57场仍未到开球105分钟后的采集时点，HTTP请求0。最早已预测赛事00:30开球，其最早轮询时点为02:15；到时仍须来源明确常规时间完场，不用推算时点代替赛果证据。Brier为空，无实战表现或上线证明。

`outputs/v20_unique_pending_settlement_20261010.json`保留本次实际执行时刻、原冻结摘要和执行代码摘要；`outputs/v20_result_collection_initial/`保留179场完整采集状态。前一份`v20_unique_pending_settlement.json`也保留。

## 命令

```sh
PYTHONPATH=. python -m beidan_bd1.unique_settlement \
 --registry outputs/v19_prospective_registry.json \
 --registry-sha256 a24eb601250826f7da688c02a547459dc6e13f1731aa424e252230e0b9691ba7 \
 --results outputs/v20_result_collection_initial/results.jsonl \
 --out outputs/unique_settlement_NEW_ACTUAL_RUN.json
```

登记表SHA256 `a24eb601250826f7da688c02a547459dc6e13f1731aa424e252230e0b9691ba7`；已与GitHub v19原文件Git blob `8826c6153bb4e46bd4dc193673bc4ba8e3bfa3e4`逐字节核对。SHA不能独立证明提供商或时钟真实性。

未来将经过原件校验的赛果JSONL作为输入，逐场交给其最早封存版本的现有结算器；重复或非研究候选赛果拒绝，不重拟合、不赛后重选。汇总保留完整去重开售池分母、逐场原清单摘要、Brier、31类比分log loss、进球偏差和原样本量门控。少于5期/500唯一成对赛果不能认定验证完成。阶段不同不能扩大样本量；中文绑定批准仍为0，生产标志始终false。

当前v19预测池覆盖登记表全部57场，可以用其采集器取得赛果后交给本工具；未来若新冻结不再包含此前全部赛事，须收集各原冻结的遗漏候选并在原件一致的前提下先形成唯一赛果输入，不能盲目相加多个采集目录。

## 验证

133项测试通过。新增测试验证：早期None选择不被后期ridge=5替换、每场只计分一次、完整池分母不缩水、原字节不改、登记表即使重新算摘要也不能挑选后期版本、重复/非候选赛果拒绝、缺赛果保留pending及null Brier、登记元数据篡改拒绝、同一来源event_id跨阶段重复拒绝。测试赛果为明确合成数据，不作为实战Brier证明。

PR #2保持draft；未合并或部署。旧冻结与原登记表保持不变。后续继续补采缺口、按真实时刻封存未来比赛，并在赛果可得时执行上述原概率结算。
