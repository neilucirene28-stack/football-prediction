# 版本变更记录

## v2.3（2026-09-29）：真实采集器 + 服务器部署

- `collector/sources/xiaodianhuo.py` 从骨架实现为真实采集器：
  HTTP 直调站内 JSON 接口（接口路径来自 v1 项目 Playwright 拦截实测），
  列表 → 按未来 7 天过滤 → 逐场抓战绩/欧指/亚指/阵容详情 → 统一为
  predict() 可直接消费的字段（home_recent/away_recent/h2h/odds/
  opening_odds/asian/injury），快照时间=抓取时刻，请求节流。
- 新增 `tests/test_collector.py`（8 项解析器单测，合成载荷、零网络）。
- 新增 `scripts/server_bootstrap.sh`（腾讯云/Oracle 通用一键部署，
  幂等；`/opt/football-v2` 与 v1 完全隔离；复用 v1 postgres 容器新建
  `football_v2` 库；cron 每 6 小时采集、每天 03:30 复盘）。
- 新增 `docs/DEPLOY.md`（部署、回滚、Oracle 迁移预案）。
- 测试：36 项通过。

## v2.2（2026-09-29）：预测链路优化

审计发现并修复了三个"算了但没用上"的环节：

1. **H2H 真正生效**：之前 `h2h_adjust` 只写进备注、从不作用于 λ。
   现在近3场交锋净胜球 → λ ±3% 封顶调整（主队视角记录）。
2. **盘口漂移接入置信度**：初盘→即时大幅漂移说明市场分歧大，
   市场稳定性因子再打折（每个漂移信号 -10%，最多两个）。
3. **Platt 校准接入预测**：`backtest.py` 的 `platt_fit` 之前没人调用。
   现在 `predict(payload, config={"platt": {"home": (A,B), ...}})` 会在融合后
   应用回测拟合的校准参数并重新归一，输出带 `calibrated` 标记。
   （参数必须来自真实回测样本拟合，禁止手填。）

新增 `scripts/sensitivity.py`：扫描 rho/decay/ht_factor，
确认关键参数小幅变化不导致预测方向翻转（当前：rho 极差 0.9%，
decay 极差 2.5%，方向全部一致，模型鲁棒）。

测试：28 项通过（含新增 test_optimize.py 4 项）。

## v2.1（2026-09-29）：吸收 V4.1 的准度改进

用户反馈旧版《专业足球量化预测模型 V4.1》“不是很准”。本轮把 V4.1 里
**对准度真正有用的部分**吸收进 v2 引擎，同时**不照搬** V4.1 的人工规则堆砌。

### 吸收了什么（为什么有用）

| V4.1 条款 | v2.1 实现 | 作用 |
|---|---|---|
| 时间衰减权重（1.00/0.90/0.82…） | `strengths.py`：权重 decay**i，默认 0.90 | 近期状态更能代表当前实力 |
| 对手强度修正 | `strengths.py`：opp_attack/opp_defense 归一化 | 对弱队刷进球不再虚高强度 |
| P_stat/P_market/P_form/P_elo 动态融合 | `fusion.py`：model/elo/market 三信号集成，缺失信号权重按比例重归一 | 避免 V4.1 五个高度相关信号重复计权 |
| 总进球 0/1/2/3/4/5+ 分布、主要区间 | `poisson.py` | 更完整的进球市场视图 |
| 半场模型 λ_HT = λ_FT × 0.44 | `poisson.py` | 半场 1X2 与比分 |
| 模型合理盘口、盘口偏差 | `market.py`：fair_handicap | 发现市场盘口定价偏差 |
| 盘口共振/背离 | `market.py`：handicap_movement | 升盘+模型支持=共振；退盘=背离风险 |
| 一致性检查（十七节） | `predictor.py`：consistency_check | 1X2/比分/亚盘方向冲突时降信心并警告 |
| Confidence 四因子公式（十八节） | `predictor.py` | 不再把信心等同于最大概率 |
| 冷门风险多因子（十三节） | `predictor.py` | 平局异常、小比分集中、背离等 |
| 冷门比分 | `predictor.py` | 第二可能结果中的最高概率比分 |
| Platt Scaling 校准 | `backtest.py` | 纠正系统性过度自信 |
| Monte Carlo（十五节） | `montecarlo.py`：完整度门控 | 数据不足时明确不运行，不伪造 |

### 刻意没照搬的（V4.1 的结构性问题）

1. **五个信号不是独立的**：V4.1 的 P_stat/P_poisson/P_form 高度相关。
   v2.1 把统计+状态+伤停都折进 λ（一个信号），只保留 model/elo/market
   三个相对独立的信号做集成。
2. **融合后一致性**：V4.1 先融合 1X2 再要求“同一矩阵生成所有市场”，顺序模糊。
   v2.1 明确：比分矩阵 → 所有衍生市场；1X2 融合只影响 1X2，不污染矩阵。
3. **人工规则权重**：V4.1 的阵容扣分、冷门加分多为拍脑袋常数。
   v2.1 保留框架但要求靠 walk-forward 回测定参（见 ARCHITECTURE 待办 5）。
4. **Monte Carlo 神话**：固定 λ 下 MC 只是解析矩阵的数值近似，不会提高准度。
   v2.1 的 MC 定位为“引擎自检”，不参与预测值。

### 下一步（按 V4.1 第二十节：模型升级必须依赖累计样本）

1. 接入真实历史数据，做 walk-forward 回测。
2. 用 Brier/LogLoss/校准误差对比：纯模型 vs 市场 vs 融合，确认每个模块的真实增益。
3. Platt 参数用回测样本拟合后，再应用到生产预测。
4. 累计样本足够前，不调整核心参数。

## v2.5（2026-09-29）

### 采集器按真实页面结构校准（浏览器只读验证）

- 卡片不是 `<a>` 包裹，JS 点击跳转：改为按"条情报"定位卡片、
  点击后从 URL 取真实 matchid（详情页路径段 326/704 会变，不再硬编码）。
- 卡片内无日期：日期取自页面头"2026-09-29 星期二"。
- 队名三行布局（朝鲜女/vs/中国女）+ 单行"A vs B" 双兼容；
  列表页缩写（朝鲜女）与详情页全称（朝鲜女足）经 `_base_name` 归一。
- 战绩表分组感知：按"交锋/主队近/客队近"组标题归属，缺失时回退队名匹配；
  日期支持 26-09-25 六位格式。
- 欧指/亚指行解析与实测一致（百家平均 6 数、Bet365 初/新盘口水位）。
- `sql/001_init.sql` 补 `uq_matches_external_source` 唯一索引
  （`ON CONFLICT DO NOTHING` 依赖它；幂等可重跑）。
- 38 项测试通过。

## v2.4（2026-09-29）

### 采集器重写：Playwright + DOM（替代 HTTP 直调）

浏览器实测结论（2026-09-29）：站内 JDD 接口请求加密、响应解密，
裸调 `apic-sport-new.jdddata.com` 直接失败；且列表/阵容接口路径
与 v1 时期已变化（list/4、lineup/3 等）。HTTP 直调路线作废。

- `sources/xiaodianhuo.py` 改为 Playwright 驱动真实页面：
  进店码进入店铺 → 解析比赛卡片 → 逐场打开详情页 → 切战绩/欧指/
  亚指/阵容 4 个 tab → 读渲染后 DOM 表格。
- 解析策略：全表扫描 + 行模式自识别（百家平均行、Bookmaker 行、
  比分正则），不依赖 CSS 类名；中文盘口（一/球半→1.25 等）映射。
- 只读公开数据；不登录、不点解锁/订阅。
- `requirements.txt` 新增 playwright；`server_bootstrap.sh` 自动安装
  Chromium（含系统依赖）。
- 36 项测试通过（含 FakePage 桩的 tab 解析测试）。
- 待生产验证：真实页面跑通后再确认选择器与字段。

- 初始重写：Elo、攻防强度、Poisson/Dixon-Coles、市场去水、融合、Kelly、复盘指标。
- 采集器插件架构、FastAPI、简易前端、PostgreSQL 表结构。
- 14 项测试通过，演示链路跑通。
