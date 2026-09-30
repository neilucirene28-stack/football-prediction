import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.adaptive_weights import (  # noqa: E402
    accumulate, hedge_weights, shadow_weights)


def _rec(signals: dict, days_ago: float, now: datetime):
    return {"at": now - timedelta(days=days_ago), **signals}


def test_hedge_weights_sum_to_one_and_favor_better():
    w = hedge_weights({"model": 10.0, "market": 20.0, "elo": 15.0}, n_eff=50)
    assert w is not None
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["model"] > w["elo"] > w["market"]  # 损失越小权重越大
    assert all(v > 0 for v in w.values())


def test_hedge_weights_uniform_on_tie():
    w = hedge_weights({"model": 5.0, "market": 5.0}, n_eff=50)
    assert abs(w["model"] - 0.5) < 1e-9


def test_hedge_weights_inactive_when_few_samples():
    assert hedge_weights({"model": 1.0, "market": 2.0}, n_eff=14.9) is None
    assert hedge_weights({"model": 1.0, "market": 2.0}, n_eff=15.0) is not None


def test_accumulate_time_decay():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    # model 久远以前好、最近差；market 相反 → 衰减后 market 损失更小
    recs = ([_rec({"model": 0.2, "market": 1.5}, 200, now) for _ in range(10)]
            + [_rec({"model": 1.5, "market": 0.2}, 2, now) for _ in range(10)])
    losses, n_eff = accumulate(recs, now)
    assert losses["market"] < losses["model"]
    assert n_eff < 20  # 衰减后有效样本 < 原始场次


def test_shadow_weights_end_to_end():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    scored = []
    for i in range(20):
        scored.append({
            "kickoff_at": now - timedelta(days=i),
            "logloss_model": 0.8, "logloss_market": 1.2, "logloss_elo": 1.0,
        })
    sh = shadow_weights(scored, now)
    assert sh["weights"] is not None
    assert sh["weights"]["model"] > sh["weights"]["market"]
    assert sh["n_eff"] > 15


def test_shadow_weights_not_activated_when_few():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    scored = [{"kickoff_at": now - timedelta(days=i),
               "logloss_model": 0.8, "logloss_market": 1.2}
              for i in range(5)]
    sh = shadow_weights(scored, now)
    assert sh["weights"] is None
    assert sh["n_eff"] < 15
