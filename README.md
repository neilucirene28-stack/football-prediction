# football-prediction-v2

一套完整重写的足球比赛量化预测项目。Python 单语言，引擎完整实现，开箱即跑。

本次修复的目标是**竞彩项目**。API与预测脚本统一使用
`engine.jingcai_predictor.predict`，只接受竞彩输入与参数；北单独立项目另行处理。
当前入口与交付范围见`docs/JINGCAI_PROJECT_SEPARATION_20261010.md`。

当前竞彩迭代为 **v13（计算版本2.11）**。新增竞彩专用强度模块、可显式启用的
主客场规范基线候选，以及原生赛事/赛季/阶段、历史对手评级时间窗口的来源门控。
默认 `strength_baseline_mode="legacy_v12"` 保留现行基线；研究用
`strength_baseline_mode="venue_normalized"`，不会自动升为默认。
22场原输入比较中，默认模式完整矩阵保持一致；规范基线候选的1-1众数反而由7场增至10场，
因此不据此宣称命中率提升或比分问题已解决。真实当季全联赛统计本轮仍缺失。
核心704项、采集70项通过；1项数据库依赖跳过，1项其他项目分支排除。
详细契约、限制与只读比较见 [v13说明](docs/JINGCAI_STRENGTH_CANDIDATE_20261011.md)。

以下为此前迭代记录：

第九轮已加入竞彩多源情报输入、原件校验、原生ID适配、分信息种类选择来源，
并接入预测输出和复盘研究导出。中国竞彩网优先；缺失保留未知。接入状态、
命令与边界见`docs/JINGCAI_SOURCE_INTELLIGENCE_20261011.md`。
本轮576项竞彩及通用测试通过、1项数据库依赖检查跳过；采集器70项通过。
情报对概率的学习系数尚未启用；代码包尚未部署至MUSE。
第十轮补齐API赛前赛程原件核验、完整球员统计分页、失败及额度停止处理、
情报采集时效与字段覆盖数量，并修复追加来源时丢失旧情报的问题。
当前621项竞彩及通用测试通过、1项数据库依赖检查跳过，采集器70项通过。
详见`docs/JINGCAI_FIXTURE_PAGINATION_20261011.md`；概率系数仍未启用。
当前第六轮新增牌数对赛果的离线增量候选、时序回测门槛和竞彩版本只读导出，
见`docs/JINGCAI_CARDS_INCREMENTAL_VALIDATION_20261010.md`；生产赛果调整尚未启用。
最新竞彩及通用验收460项通过、1项跳过；采集器70项通过。
第五轮新增赛季红黄牌数据接入口、精确球队/时间绑定、牌数复盘，
并统一半场联合边际和最终矩阵蒙特卡洛。435项竞彩及通用检查、70项采集检查通过；
真实当前赛季数据与牌数对赛果的影响系数仍须接入及验证。详见
`docs/JINGCAI_CARDS_HT_MC_REPAIR_20261010.md`。

## 架构一览

```
collector/      数据采集（Source 插件式，可接真实站或跑内置演示源）
engine/         预测引擎：Elo → 攻防强度 → Dixon-Coles 比分矩阵 → 市场融合 → Kelly
api/            FastAPI 服务：比赛列表 / 预测 / 复盘 + 简易前端
sql/            Postgres 表结构
tests/          pytest 单元测试（引擎数学）
scripts/        一键演示脚本
docs/           架构说明与方法论文档
```

方法论：`docs/METHODOLOGY.md`（Elo → Dixon-Coles → 市场去水融合 → walk-forward 回测，
参考 Hicruben/world-cup-2026-prediction-model 的透明可复现路线）。

## 快速启动（演示模式，无需数据库）

```bash
cd football-prediction-v2
python3 -m pip install -r api/requirements.txt  # 或只装 numpy
python3 scripts/demo_predict.py                 # 跑两场样例比赛的完整预测
python3 -m pytest tests/ -q                     # 跑引擎测试
```

## 完整启动（含 Postgres）

```bash
cp .env.example .env
docker compose up --build
# API: http://localhost:8000        前端: http://localhost:8000/
# 采集: docker compose --profile collect up collector
```

## API

- `GET  /api/matches` — 比赛列表（按开球时间）
- `GET  /api/matches/{id}` — 比赛详情（含赔率快照）
- `POST /api/predict` — 输入比赛+历史数据，返回 V4.2 格式完整预测
- `GET  /api/backtest/summary` — 复盘简报（Brier / LogLoss / 校准）

## 数据纪律

- 引擎只消费开赛前快照（`collected_at < kickoff_at`），回测严格 walk-forward。
- 数据缺失走降级模型；D 级数据拒给概率（见 `docs/METHODOLOGY.md`）。
- 预测是概率陈述，不是承诺；`engine` 不下单、不碰资金。
## 2026-10-10 竞彩链路修复

竞彩整数让球结算、同源比分矩阵、让平风险展示及时间记录已修复。详见
`docs/JINGCAI_LETDRAW_REPAIR_20261010.md`；可运行
`python3 scripts/audit_jingcai_letdraw.py` 重现逐场审计。旧回填不含完整赛前证据，
审计重建结果不能当作已认证的无泄漏新模型回测。

第二轮已补充竞彩输入数值与时间证据校验、最近交锋取样、自适应比分网格和
半全场边际严格对齐。第二轮行为与416项通过的验收见
`docs/JINGCAI_INPUT_GRID_REPAIR_20261010.md`；运行
`python3 scripts/audit_jingcai_grid.py` 可重现网格数值诊断。北单保持原计算策略。

第三轮已修复竞彩原输入绑定、封存时间门槛、写入状态、版本隔离复盘、
同样本权重诊断、各玩法评分及批量正式让球入口。旧校准参数缺少版本与时序
来源证据时不再自动启用。当前累计验收为460项核心测试通过、1项跳过，
采集器70项通过；详见`docs/JINGCAI_LEDGER_REVIEW_REPAIR_20261010.md`。
复盘命令为`python3 scripts/scoreboard.py --days 30`，读取实际预测账本；
旧回填缺少原快照时不得当作真实赛前学习样本。

入口隔离修正后，当前只按竞彩范围验收：竞彩及通用依赖391项通过、1项跳过，
采集器70项通过。北单专用测试文件不在本轮交付与验收范围；前述460项是
第三轮旧双模式兼容性验收数字，当前命令以项目隔离报告为准。


## 竞彩 v12 独立核心

生产调用使用 `engine.jingcai_predictor.predict`；业务流程、默认配置和让平先验独立持有，默认校准文件为 `engine/jingcai_calibration.json`，当前禁用。通用数学工具仍复用。范围、接入和验证边界见 [独立核心说明](docs/JINGCAI_INDEPENDENT_CORE_20261011.md)。
