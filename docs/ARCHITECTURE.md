# 架构说明

## 分层

```
collector/  采集：Source 插件（demo / xiaodianhuo骨架）→ 校验 → DB/JSON
engine/     引擎：纯函数，无 DB 依赖，可独立测试
api/        服务：FastAPI，三组路由 + 静态前端
sql/        Postgres 表结构（docker entrypoint 自动执行）
```

## 数据流

1. `collector` 定时/单次抓取，写入 `matches` + `odds_snapshots`
  （快照时间 `collected_at` 必须早于 `kickoff_at`）。
2. 前端/API 调用 `POST /api/predict`，`engine.predictor.predict()` 依次执行：
   输入校验 → 完整度评分 → λ估计 → 比分矩阵 → 市场去水 → 融合 →
   价值检测 → 衍生市场 → 置信度/风险 → V4.2 格式输出。
3. 预测写入 `predictions` 表；完赛后由复盘任务写入 `backtest_results`，
   `GET /api/backtest/summary` 聚合展示。

## 关键设计决策

- **Python 单语言**：v1 是 Node 采集 + Python 后端，v2 统一 Python，降低维护成本。
- **引擎与 IO 解耦**：`engine/` 不读 DB、不调网络，全部输入经参数传入；
  因此 `pytest` 无需任何外部服务即可验证全部数学。
- **降级优先**：DB 不可用时采集写 JSON、API 用演示数据，
  `make demo` 全程离线可跑。
- **LLM 的位置**：引擎只产出数字；文字解读（如需）由独立 LLM 服务消费
  引擎输出，不允许 LLM 篡改概率（v1 的 deepseek_analysis 保持此边界）。

## 与 v1 的对应关系

| v1（football-prediction） | v2 |
|---|---|
| collector/Node.js | collector/Python（Source 插件式） |
| normalizer/CanonicalMatch | 简化为 DB 行 + payload 校验（v2 暂不做多源归一） |
| prediction.py（stub，拒绝预测） | engine/（完整实现） |
| deepseek_analysis | 未内置；按需外挂，只读引擎输出 |
| tests/test_historical_backtest.py | engine/backtest.py + /api/backtest/summary |

## 待办（按优先级）

1. 实现 `XiaoDianHuoSource._fetch_list/_fetch_detail`（参考 v1 的 8 标签页结构）。
2. 复盘定时任务：完赛 → 自动评分 → 写 backtest_results → Platt 校准更新。
3. Elo/ρ/ht_factor 按联赛拟合（目前为全局常数）。
4. Monte Carlo 赛季模拟（冠军/降级概率）。
5. 真实 walk-forward 回测：用历史数据验证时间衰减、对抗修正、Elo 融合是否真带来增益（Brier/LogLoss/校准误差 vs 市场基线）。

## 版本记录

- **v2.1（2026-09-29）**：吸收 V4.1 的准度相关改进——时间衰减 + 对手强度修正（`strengths.py`）、
  model/elo/market 三信号动态集成（`fusion.py`）、总进球分布 + 主要区间 + 半场模型
  （`poisson.py`）、模型合理盘口 + 盘口共振/背离（`market.py`）、一致性检查、
  置信度 = 数据质量 × 模型一致 × 市场稳定 × 阵容确定、冷门比分（`predictor.py`）、
  Platt Scaling 概率校准（`backtest.py`）、完整度门控 Monte Carlo（`montecarlo.py`）。
  详见 `docs/CHANGELOG.md`。
- **v2.0（2026-09-28）**：初始重写，Elo/Poisson-Dixon-Coles/市场去水/Kelly/复盘指标链路跑通。
