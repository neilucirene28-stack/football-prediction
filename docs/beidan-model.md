# 北单模型说明（docs/beidan-model.md）

## 北单模型 vs 竞彩模型：什么关系？

**一句话：共享数学引擎，独立校准/特征/输出。**

```
                    ┌─────────────────────────┐
                    │  数学引擎（共享，不重写）  │
                    │  Poisson + Dixon-Coles   │
                    │  + Elo + 市场融合 (v2.5)  │
                    │  letdraw.py / 弱队收缩    │
                    └────────┬────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ 竞彩链路  │  │ 北单适配层 │  │ (未来)   │
        │ model=    │  │ model=    │  │          │
        │ 'jingcai' │  │ 'beidan'  │  │          │
        └──────────┘  └──────────┘  └──────────┘
```

### 共享的部分（不重写）
- `engine/poisson.py`：Dixon-Coles 比分矩阵
- `engine/elo.py`：Elo 评分
- `engine/fusion.py`：三信号融合
- `engine/letdraw.py`：让平单独建模（v2.5）
- `engine/predictor.py`：主预测流程（加 `model` 参数区分）

### 北单独立的部分（`engine/beidan_*.py`）
1. **`beidan_calibration.py`**：北单专属 Platt 校准
   - 参数文件：`engine/beidan_calibration.json`（与 `engine/calibration.json` 隔离）
   - 训练数据：北单26101期55场（2026-09-30预测 vs 实际）
   - 按玩法分别校准：胜平负/让球/总进球
   - 关键发现：让球模型严重过度自信（0.52→实际0.29），校准后大幅下调

2. **`beidan_upset.py`**：冷门分层（预测"首选翻车"）
   - 输出翻车风险分（0-1），用于稳胆筛选
   - 实证发现（26101期55场）：模型越自信，翻车率越高（过度自信）
     - 低风险组：47.1% 翻车率（8/17）
     - 中风险组：58.6% 翻车率（17/29）
     - 高风险组：66.7% 翻车率（6/9）
   - 高/低风险组差异：19.6个百分点
   - 限制：无市场赔率数据，仅用模型侧特征；样本小（55场）

3. **`beidan_adjusted_goals.py`**：Adjusted Goals（垃圾时间贬值）
   - 70分钟后领先方进球线性贬值，90分钟进球只算0.5球
   - 状态：逻辑已实现并测试，**默认关闭**
   - 未进生产原因：26101期无进球分钟数据，无法做离线实验验证

### 调用方式
```python
from engine import predict

# 竞彩（默认，现有行为不变）
result_jc = predict(payload)

# 北单（启用适配层）
result_bd = predict(payload, model="beidan")
# result_bd["beidan"] 包含：
#   - calibrated_p_top_sp: 校准后的胜平负首选概率
#   - calibrated_p_top_rq: 校准后的让球首选概率
#   - upset_risk: 翻车风险分（0-1）
#   - upset_risk_tier: 低/中/高
#   - playtypes: ["胜平负", "让球胜平负", "比分", "总进球", "半全场", "上下单双"]
```

### 为什么不是完全独立的模型？
全平台调研结论：足球预测模型层已收敛（Elo+Dixon-Coles是事实标准，
1X2天花板RPS~0.175）。分水岭在市场关系和玩法适配，不在重写数学。
北单的六玩法（多上下单双）是输出层差异，校准参数是数据层差异，
都不需要重写 Poisson/Elo。
