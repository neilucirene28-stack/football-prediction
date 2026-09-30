"""Hedge 自适应融合权重 —— 影子模式（MVP-4）。

机制（照搬 betting-agent 的设计，按我们三信号重写）：
    w_i ∝ exp(-η · L_i)，η = sqrt(8 ln K / n_eff)   ← 教科书公式，非拟合常数
    L_i = 衰减累积 log-loss（90 天半衰期）
    n_eff = 有效样本量；< 15 时权重不激活，回退静态权重

纪律：
- 只读 settlements/predictions 做离线计算，绝不在线写回生产权重
- 输出"如果用了自适应权重会是什么结果"，与静态权重并排对比 2–4 周
- 结构参数（rho/decay/级别修正）不在这里动
- 不用准确率做权重依据（用 log-loss，proper scoring rule）
"""
import math
from datetime import datetime, timezone

HALF_LIFE_DAYS = 90.0
MIN_N_EFF = 15.0


def _decay(age_days: float, half_life: float = HALF_LIFE_DAYS) -> float:
    return 0.5 ** (age_days / half_life)


def hedge_weights(losses: dict, n_eff: float) -> dict | None:
    """losses: {signal: 衰减累积 log-loss}。n_eff 不足返回 None（不激活）。"""
    if n_eff < MIN_N_EFF or not losses:
        return None
    k = len(losses)
    eta = math.sqrt(8.0 * math.log(k) / n_eff) if k > 1 else 0.0
    raw = {s: math.exp(-eta * L) for s, L in losses.items()}
    total = sum(raw.values())
    if total <= 0:
        return None
    return {s: w / total for s, w in raw.items()}


def accumulate(records: list[dict], now: datetime | None = None,
               half_life: float = HALF_LIFE_DAYS):
    """records: [{signal: logloss, ... , "at": datetime}]（单场多信号）。

    返回 (losses, n_eff)：losses 为每信号衰减累积损失，n_eff 为有效样本量。
    同一时间点的多个信号共享一份时间权重。
    """
    now = now or datetime.now(timezone.utc)
    losses: dict[str, float] = {}
    n_eff = 0.0
    seen_times = set()
    for rec in records:
        at = rec.get("at")
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        age_days = max((now - at).total_seconds() / 86400.0, 0.0)
        d = _decay(age_days, half_life)
        key = at.isoformat()
        if key not in seen_times:
            seen_times.add(key)
            n_eff += d
        for sig, ll in rec.items():
            if sig == "at" or not isinstance(ll, (int, float)):
                continue
            losses[sig] = losses.get(sig, 0.0) + d * ll
    return losses, n_eff


def shadow_weights(scored: list[dict], now: datetime | None = None):
    """从看板 scored 行（含 logloss_model/market/elo 与 kickoff_at）算影子权重。

    返回 {"weights": {...} | None, "n_eff": x, "losses": {...}}。
    """
    records = []
    for s in scored:
        rec = {"at": s.get("kickoff_at")}
        for sig in ("model", "market", "elo"):
            ll = s.get(f"logloss_{sig}")
            if ll is not None:
                rec[sig] = ll
        if len(rec) > 1:
            records.append(rec)
    losses, n_eff = accumulate(records, now)
    return {"weights": hedge_weights(losses, n_eff),
            "n_eff": round(n_eff, 2),
            "losses": {k: round(v, 4) for k, v in losses.items()}}
