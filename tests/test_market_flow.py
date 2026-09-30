"""engine/market_flow.py 测试：drift / sharp_divergence / volume_weight。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.market_flow import (  # noqa: E402
    apply_volume_weight, avg_probs, dewig, drift_features, flow_features,
    liquidity_weight, movement_features, sharp_divergence, sharp_names,
    sharp_weighted_market)


def _co(open_, live):
    return {"open": list(open_), "live": list(live)}


def test_dewig_sums_to_one():
    p = dewig((2.0, 3.5, 4.0))
    assert p is not None and abs(sum(p) - 1.0) < 1e-9
    assert dewig((1.0, 2.0, 3.0)) is None      # 非法赔率
    assert dewig(None) is None
    assert dewig("x") is None


def test_drift_avg_direction():
    # 主胜赔率下降 → 主胜概率漂移为正
    companies = {
        "A": _co((2.5, 3.2, 3.0), (2.0, 3.4, 4.0)),
        "B": _co((2.6, 3.1, 2.9), (2.1, 3.3, 3.9)),
    }
    d = drift_features(companies)
    assert d["avg"] is not None
    assert d["avg"]["home"] > 0 > d["avg"]["away"]
    assert d["coverage"] == "full"
    assert d["n_companies"] == 2


def test_drift_missing_open_is_none_but_live_ok():
    companies = {"A": {"open": None, "live": [2.0, 3.4, 4.0]}}
    d = drift_features(companies)
    assert d["avg"] is None            # 初盘缺失 → drift 缺失（合法输出）
    assert d["coverage"] == "none"
    assert d["n_companies"] == 1


def test_drift_partial_coverage():
    companies = {
        "A": _co((2.5, 3.2, 3.0), (2.0, 3.4, 4.0)),
        "B": {"open": None, "live": [2.1, 3.3, 3.9]},
    }
    d = drift_features(companies)
    assert d["coverage"] == "partial"
    assert d["avg"] is not None  # 有一家初盘即可算平均漂移


def test_sharp_names_lowest_overround():
    # A 水位最低 → sharp；B 水位最高 → 零售
    companies = {
        "sharp1": _co((2.0, 3.5, 4.0), (1.95, 3.45, 3.95)),
        "retail": _co((2.0, 3.5, 4.0), (1.70, 3.00, 3.40)),
    }
    names = sharp_names(companies, n=1)
    assert names == ["sharp1"]


def test_sharp_divergence_zero_when_agree():
    row = {"open": [2.5, 3.2, 3.0], "live": [2.0, 3.4, 4.0]}
    companies = {f"C{i}": dict(row) for i in range(10)}
    assert sharp_divergence(companies) == 0.0


def test_sharp_divergence_positive_on_split():
    retail = {"open": [2.5, 3.2, 3.0], "live": [2.40, 3.20, 3.10]}
    companies = {f"retail{i}": dict(retail) for i in range(8)}
    companies["sharp1"] = {"open": [2.5, 3.2, 3.0],
                           "live": [1.70, 3.80, 5.00]}  # 水位更低、方向迥异
    assert sharp_names(companies, n=1) == ["sharp1"]
    assert sharp_divergence(companies) > 0.015


def test_sharp_divergence_none_when_no_rest():
    companies = {"A": _co((2.5, 3.2, 3.0), (2.0, 3.4, 4.0))}
    assert sharp_divergence(companies) is None  # 全是 sharp，无"其余"可比


def test_sharp_weighted_market_sums_to_one():
    companies = {
        "A": _co((2.5, 3.2, 3.0), (2.0, 3.4, 4.0)),
        "B": _co((2.6, 3.1, 2.9), (2.1, 3.3, 3.9)),
    }
    p = sharp_weighted_market(companies)
    assert p is not None and abs(sum(p) - 1.0) < 1e-6


def test_liquidity_weight_polymarket_tiers():
    assert liquidity_weight(pm_volume_usdc=2_000_000)["volume_weight"] == 1.0
    assert liquidity_weight(pm_volume_usdc=2_000_000)["source"] == "polymarket"
    assert liquidity_weight(pm_volume_usdc=50_000)["volume_weight"] == 0.70
    assert liquidity_weight(pm_volume_usdc=5_000)["volume_weight"] == 0.55
    # volume=0/None → 降级用公司数量，不编造
    r = liquidity_weight(n_companies=40, pm_volume_usdc=0)
    assert r["source"] == "company_count" and r["volume_weight"] == 0.80
    r = liquidity_weight(n_companies=3)
    assert r["volume_weight"] == 0.35


def test_liquidity_weight_missing():
    r = liquidity_weight()
    assert r["source"] == "missing" and r["volume_weight"] == 0.25


def test_apply_volume_weight_scales_market_only():
    w = {"model": 0.5, "market": 0.35, "elo": 0.15}
    w2 = apply_volume_weight(w, 1.0)
    assert abs(w2["market"] - 0.35) < 1e-9          # v=1 不动
    assert abs(sum(w2.values()) - 1.0) < 1e-9
    w3 = apply_volume_weight(w, 0.0)
    assert w3["market"] < w["market"]               # 流动性差 → 市场降权
    assert w3["market"] >= 0.35 * 0.5 - 1e-9       # floor=0.5 保留一半
    assert abs(sum(w3.values()) - 1.0) < 1e-9
    assert w3["model"] > w["model"]                 # 让出的权重归模型/Elo


def test_apply_volume_weight_no_market_passthrough():
    w = {"model": 1.0, "market": 0.0, "elo": 0.0}
    assert apply_volume_weight(w, 0.1) == w


def test_flow_features_missing_safe():
    f = flow_features({})
    assert f["drift_avg"] is None
    assert f["sharp_divergence"] is None
    assert f["liquidity_source"] == "missing"
    assert f["n_companies"] == 0


def test_flow_features_end_to_end():
    companies = {
        "A": _co((2.5, 3.2, 3.0), (2.0, 3.4, 4.0)),
        "B": _co((2.6, 3.1, 2.9), (2.1, 3.3, 3.9)),
        "C": _co((2.4, 3.3, 3.1), (1.9, 3.5, 4.2)),
    }
    f = flow_features(companies)
    assert f["drift_avg"]["home"] > 0
    assert f["sharp_weighted_market"] is not None
    assert f["liquidity_source"] == "company_count"
    assert 0.0 <= f["volume_weight"] <= 1.0


def test_avg_probs_none_when_empty():
    assert avg_probs([]) is None
    assert avg_probs([None, None]) is None


def test_movement_features_agreement():
    movement = {
        "A": {"n": 3, "points": [
            {"home": 2.0, "draw": 3.5, "away": 3.5, "time": "09-27 10:00"},
            {"home": 1.9, "draw": 3.5, "away": 3.8, "time": "09-28 10:00"},
            {"home": 1.8, "draw": 3.6, "away": 4.0, "time": "09-29 10:00"},
        ]},
        "B": {"n": 1, "points": [
            {"home": 2.0, "draw": 3.5, "away": 3.5, "time": "09-27 10:00"},
        ]},
    }
    companies = {"A": {"open": [2.0, 3.5, 3.5], "live": [1.8, 3.6, 4.0]}}
    mf = movement_features(movement, companies)
    assert mf["n_companies"] == 2
    assert mf["median_n_points"] == 3
    # 走势与快照同向（主胜概率都上升）-> 一致率 1.0
    assert mf["drift_direction_agreement"] == 1.0
    assert mf["n_compared"] == 1
    assert mf["late_steam_avg"] is not None
    assert mf["max_excursion_avg"] is not None


def test_movement_features_disagreement():
    movement = {"A": {"n": 2, "points": [
        {"home": 2.0, "draw": 3.5, "away": 3.5, "time": "09-27 10:00"},
        {"home": 2.5, "draw": 3.2, "away": 2.8, "time": "09-29 10:00"},
    ]}}
    companies = {"A": {"open": [2.0, 3.5, 3.5], "live": [1.8, 3.6, 4.0]}}
    mf = movement_features(movement, companies)
    assert mf["drift_direction_agreement"] == 0.0
    assert mf["n_compared"] == 1


def test_movement_features_empty():
    mf = movement_features({})
    assert mf["n_companies"] == 0
    assert mf["drift_direction_agreement"] is None
    assert mf["late_steam_avg"] is None
