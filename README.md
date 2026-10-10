# football-prediction-v2

一套完整重写的足球比赛量化预测项目。Python 单语言，引擎完整实现，开箱即跑。

## 北单独立研究分支当前进度

`codex/beidan-bd1-v13-import` / 草稿 PR #2 对应的本地研究成果已推进至 v23，待上传；续接说明见
[`beidan_independent/docs/beidan-v23-first-verified-results.md`](beidan_independent/docs/beidan-v23-first-verified-results.md)。
当前179场完整池、625场显式FT/HT来源历史、跨版本66场唯一已封存研究预测、113场未覆盖、19场成对已核验赛果，另47场等待赛果。
最早合格原概率结算 Brier=0.20622960801176707，与已选基线相同；1期/19场尚未满足5期/500场门槛。逐场复盘见
[`beidan_independent/outputs/review_20261010_v23.md`](beidan_independent/outputs/review_20261010_v23.md)。
新冻结只对仍未开球的64场生成概率；另2场使用更早的原封存，不能赛后重写。中文身份未批准，官方让球/SP未导入，生产资格false。
独立模块验证：`cd beidan_independent && PYTHONPATH=. python -m unittest discover -s tests -q`。

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
