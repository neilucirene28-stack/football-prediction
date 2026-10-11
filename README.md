# football-prediction-v2

一套完整重写的足球比赛量化预测项目。Python 单语言，引擎完整实现，开箱即跑。

## 北单独立研究分支当前进度

`codex/beidan-bd1-v13-import` / 草稿 PR #2：本地已完成v30，待Muse上传。最新纪律特征报告：[beidan-v30-discipline.md](beidan_independent/docs/beidan-v30-discipline.md)。官方资源阶段报告：
[`beidan_independent/docs/beidan-v29-official-resources-and-timer.md`](beidan_independent/docs/beidan-v29-official-resources-and-timer.md)。
当前179场完整池、1231场FT/显式HT历史、95场唯一赛前预测、19场已核验赛果、76场待赛果；旧95场最早归属及原Brier 0.20622960801176707不改。
v28接通最早封存赛果到下一次真实研究择参：核验原注册表/原冻结，只读取原先声明的候选概率；19场已回流但不足30场门槛，仍保留L3先验，不赛后替换旧正式模型。新76条全部是后续重复，不增加独立样本；新测试赛果保持空。
六项描述性校准保留于v27，不拟合校准器，L1仍作诊断：
[`beidan_independent/outputs/v27_verified_collection_workflow_20261010/calibration.md`](beidan_independent/outputs/v27_verified_collection_workflow_20261010/calibration.md)。
v29保留27份官方HTTP原件（21成功、6访问失败），解析六场目标J2及K1/K2本月46场，提出12场当日原生赛程候选，尚不增加合格预测。修复采集器迁移时误读旧目录；221项测试通过（38.715秒）。验证：`cd beidan_independent && PYTHONPATH=. python -m unittest discover -s tests -q`。
v30新增红黄牌与犯规的可核验历史特征：46场、20队，保留缺失值、同阶段筛选和实际数据可用时间。仅是部分赛季观察，尚未训练未来吃牌概率或调整进球模型。227项测试通过（38.845秒）。用户提供的小店伙北单页面当前浏览器未加载赛事列表，尚未接入其赔率/交易字段。
规范身份及官方让球/SP未批准，1期19场未满足5期/500场，生产资格false；定时采集周期已试跑，服务器服务模板待部署。最早赛果检查为北京时间12:45，来源明确FT方可结算。
84场未预测的v27缺口账本保留：41场已开球不能补造，38场未开球缺来源、5场历史不足。

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
