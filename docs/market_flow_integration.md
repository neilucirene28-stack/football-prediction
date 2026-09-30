# 资金流向 predictor 接入说明（影子模式）

> 给父代理：predictor.py 由你统一改。以下为精确接入点、函数签名、调用示例。
> 纪律：新信号先进影子 2–4 周，生产权重不动。

## 1. 接入点

`engine/predictor.py` 的 `predict()` 中分两处插入（注意顺序：
`weights` 是第 7 节 `ensemble_weights(...)` 之后才存在的，不能提前用）。

**6b（第 6 节之后、第 7 节之前）：只算 flow 特征。**

```python
# ---- 6b. 资金流特征（影子，不进生产融合） ----
from .market_flow import apply_volume_weight, flow_features
flow = None
flow_shadow = None
companies = (payload.get("raw") or {}).get("x12_companies")
if companies and p_market is not None:
    pm = payload.get("polymarket") or {}
    flow = flow_features(companies,
                         pm_volume_usdc=pm.get("volume_usdc"))
```

**7b（第 7 节 `weights = ensemble_weights(...)` 之后）：算影子权重与影子概率。**

```python
# ---- 7b. 资金流影子（生产 p_final 不用 w_flow） ----
if flow is not None:
    w_flow = apply_volume_weight(weights, flow["volume_weight"])
    flow_shadow = {
        "weights": {k: round(v, 4) for k, v in w_flow.items()},
        "p_home": None, "p_draw": None, "p_away": None,  # 下行填充
    }
    pf = ensemble({"model": p_model, "market": p_market, "elo": p_elo},
                  w_flow)
    flow_shadow.update({"p_home": round(pf[0], 4),
                        "p_draw": round(pf[1], 4),
                        "p_away": round(pf[2], 4)})
```

`ensemble` 已在 predictor 顶部从 `.fusion` 导入。

result 返回体中追加（与 `signals`/`weights` 并列）：

```python
"flow": flow,            # 见 §3 输出格式；无公司数据时为 None
"flow_shadow": flow_shadow,
```

生产 `p_final` 保持 `ensemble(signals, weights)` 不变（用第 7 节原始
`weights`，不用 `w_flow`）。

## 2. batch 数据 plumbing（`scripts/batch_predict_today.py`）

在构造 payload 之后、`predict()` 之前加（best-effort，绝不阻塞预测）：

```python
try:
    from scripts.polymarket_flow import fetch_match_flow
    # 英文队名：复用红黄牌 MVP 的中英映射表（无映射 → 传中文名，搜不到即 missing）
    pm = fetch_match_flow(home_en, away_en)
    if pm.get("status") in ("ok", "partial"):
        m["polymarket"] = {"volume_usdc": pm.get("volume_usdc"),
                           "event_slug": pm.get("event_slug"),
                           "captured_at": pm.get("captured_at")}
except Exception:
    pass  # 拿不到 → volume_usdc=None → 降级用公司数量
```

`persist.py` 无需改动：`payload` 全量快照已包含 `raw.x12_companies`、
`raw.x12_movement`（指数走势，零额外请求）和新增的 `polymarket` 块。
亚指/大小球走势（`raw.asian_movement`/`raw.ou_movement`）仅在
`fetch_matches(with_trends=True)` 时抓取（每家公司 2 次请求），
默认关闭，批量回测不用。

## 3. `flow_features` 输出格式

```python
{
  "drift_avg": {"home": +0.021, "draw": -0.008, "away": -0.013} | None,
  "drift_sharp": {...} | None,      # sharp 子集的漂移
  "drift_coverage": "full|partial|none",
  "n_companies": 42, "n_sharp": 8,
  "sharp_divergence": 0.012,        # sharp 均值 vs 其余公司均值 max|Δp|
  "sharp_weighted_market": [0.52, 0.27, 0.21] | None,
  "volume_weight": 0.8,             # ∈[0,1]
  "liquidity_tier": "wide",
  "liquidity_source": "polymarket|company_count|missing",
}
```

`drift_avg is None`（coverage=none）→ 该场退回纯即时赔率，
`flow` 仍记录，`flow_shadow["weights"]` == 生产权重（v 不改变无 market 的情况；
有 market 无初盘时 volume_weight 照常调节）。

## 4. 函数签名（`engine/market_flow.py`）

```python
def flow_features(companies: dict, pm_volume_usdc=None) -> dict
def drift_features(companies: dict) -> dict        # avg/sharp 漂移 + 覆盖率
def sharp_divergence(companies: dict) -> float | None
def sharp_weighted_market(companies: dict) -> tuple | None  # 1.5×/0.7× 加权
def liquidity_weight(n_companies=None, pm_volume_usdc=None) -> dict
def apply_volume_weight(weights: dict, volume_weight: float,
                        floor: float = 0.5) -> dict
def movement_features(movement: dict, companies: dict | None = None) -> dict
```

`apply_volume_weight` 语义：`w_market *= (0.5 + 0.5·v)` 后重归一；
`v=1` 时权重不变；无 market 信号时原样返回。

`movement_features(movement, companies)`（指数走势特征，影子/诊断用，
不进生产融合）：

```python
movement = (payload.get("raw") or {}).get("x12_movement")  # 采集器已存
mf = movement_features(movement, companies)
# {
#   "n_companies": 64, "median_n_points": 37,
#   "drift_direction_agreement": 1.0,  # 走势隐含drift方向 vs 快照drift方向一致率
#   "n_compared": 62,
#   "late_steam_avg": 0.0098,   # 尾部25%平均变动幅度（late steam探测输入）
#   "max_excursion_avg": 0.05,  # 相对首点的最大偏离（均值）
# }
```

数据粒度诚实标注：`key_change_points`（每次赔率变动一个点，非等间隔；
时间戳只有 "MM-DD HH:MM"）。无走势数据时全字段 None/0，缺失安全。

## 5. 影子期规则（2–4 周）

- 生产融合、信心、输出格式：零改动。
- 评估：`scripts/flow_walkforward.py --db` 读 predictions（含 flow_shadow）
  ⋈ settlements，对比 T2（生产）vs T3（影子）的 Brier/LogLoss/RPS/ECE。
- 切换门槛：n ≥ 100 且 T3 在 Brier 和 LogLoss 上同时优于 T2，
  否则继续影子或废弃。
- 禁止事项：不把 volume_weight 做成第 4 个概率；不回填初盘/成交量；
  不碰 Titan007 会员/情报/AI 解读/HOT 模块。

## 6. 当前回测结论（2026-09-30，Titan007 无泄漏回测）

见 `scripts/flow_walkforward.py --titan-backtest` 输出。
历史回测中 Polymarket 成交量一律视为缺失（无法回溯开球前快照），
流动性代理降级为公司数量，报告中明确标注。
