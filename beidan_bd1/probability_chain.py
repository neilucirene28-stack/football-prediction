"""Optional, unvalidated same-event market and draw calibration candidate.

The default (weight=0, draw_bias=0, temperature=1) is exact identity.
Nonidentity parameters are shadow experiments until chronological validation.
"""
from __future__ import annotations

import math

from .baseline import _vectors_from_matrix
from .snapshot import _datetime


_OUTCOMES = ("胜", "平", "负")


def market_wdl(observation: dict, *, asof_at: str) -> dict[str, float]:
    """Use only true decimal pre-match 1X2 odds; BD handicap SP is not 1X2."""
    if not isinstance(observation, dict) or set(observation) != {
        "event_space", "period", "line", "odds_type", "selection_order",
        "odds", "available_at", "source"
    }:
        raise ValueError("市场事件元数据不完整")
    if (observation["event_space"] != "unhandicapped_wdl"
            or observation["period"] != "full_time" or observation["line"] is not None
            or observation["odds_type"] != "european_decimal"
            or observation["selection_order"] != list(_OUTCOMES)):
        raise ValueError("市场事件空间不匹配或SP类型未获支持")
    if (not isinstance(observation["source"], str) or not observation["source"]
            or _datetime(observation["available_at"], "available_at")
            > _datetime(asof_at, "asof_at")):
        raise ValueError("市场来源或赛前可用时间无效")
    odds = observation["odds"]
    if (not isinstance(odds, list) or len(odds) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or v <= 1 for v in odds)):
        raise ValueError("欧洲赔率须为三个大于1的有限十进制赔率")
    inv = [1 / v for v in odds]
    margin = sum(inv)
    if not .8 <= margin <= 1.4:
        raise ValueError("市场赔率边际超出审计区间")
    return {k: v / margin for k, v in zip(_OUTCOMES, inv)}


def reweight_score_matrix(score: dict[str, float], *, q: dict[str, float]) -> list[list[float]]:
    """One-shot region scaling: preserve conditional score shape in H/D/A."""
    if set(q) != set(_OUTCOMES) or any(not math.isfinite(v) or v <= 0 for v in q.values()) or abs(sum(q.values()) - 1) > 1e-8:
        raise ValueError("校准胜平负向量非法")
    cells = [tuple(map(int, label.split("-"))) for label in score]
    bound = max(max(i, j) for i, j in cells) + 1
    if bound > 81:
        raise ValueError("比分矩阵超出数值预算")
    matrix = [[0.0] * bound for _ in range(bound)]
    region = {"胜": 0.0, "平": 0.0, "负": 0.0}
    for (i, j), p in zip(cells, score.values()):
        if not math.isfinite(p) or p < 0:
            raise ValueError("基础比分概率非法")
        matrix[i][j] = p
        region["胜" if i > j else ("平" if i == j else "负")] += p
    if abs(sum(region.values()) - 1) > 1e-8 or any(v <= 0 for v in region.values()):
        raise ValueError("比分矩阵不完整或某胜平负区域没有支持")
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            key = "胜" if i > j else ("平" if i == j else "负")
            row[j] = p * q[key] / region[key]
    return matrix


def shadow_adjust(prediction: dict, *, asof_at: str,
                  market: dict | None = None, weight: float = 0.0,
                  draw_bias: float = 0.0, temperature: float = 1.0) -> dict:
    """Return new coherent six-market vectors; never mutate the base forecast."""
    if (not isinstance(weight, (int, float)) or isinstance(weight, bool)
            or not math.isfinite(weight) or not 0 <= weight <= 1):
        raise ValueError("融合权重不在[0,1]")
    if (not isinstance(draw_bias, (int, float)) or isinstance(draw_bias, bool)
            or not math.isfinite(draw_bias) or abs(draw_bias) > 3):
        raise ValueError("平局偏置超出试验范围")
    if (not isinstance(temperature, (int, float)) or isinstance(temperature, bool)
            or not math.isfinite(temperature) or not .5 <= temperature <= 3):
        raise ValueError("温度超出试验范围")
    base = prediction["vectors"]
    p = base["wdl"]
    if weight > 0 and market is None:
        raise ValueError("非零市场权重但没有合格赛前同事件市场")
    m = market_wdl(market, asof_at=asof_at) if market is not None else None
    s = {k: (1 - weight) * p[k] + weight * m[k] if m is not None else p[k]
         for k in _OUTCOMES}
    logits = {"胜": math.log(s["胜"]) / temperature - draw_bias / 2,
              "平": math.log(s["平"]) / temperature + draw_bias,
              "负": math.log(s["负"]) / temperature - draw_bias / 2}
    top = max(logits.values())
    exps = {k: math.exp(v - top) for k, v in logits.items()}
    z = sum(exps.values())
    q = {k: v / z for k, v in exps.items()}
    before = sum(sum(map(int, label.split("-"))) * v for label, v in base["score"].items())
    if weight == 0 and draw_bias == 0 and temperature == 1:
        return {"vectors": base, "score_31": prediction["score_31"],
                "wdl_before": p, "wdl_after": p,
                "goals_before": before, "goals_after": before,
                "market_wdl": m, "parameters_unvalidated": True,
                "market_provenance": None}
    matrix = reweight_score_matrix(base["score"], q=q)
    fractions = prediction.get("ht_fractions")
    if fractions is None:
        raise ValueError("缺少半场进球比例，无法重新生成完整六玩法")
    vectors, score31 = _vectors_from_matrix(matrix, fractions["home"],
                                            fractions["away"], prediction["handicap"])
    after = sum((i + j) * v for i, row in enumerate(matrix) for j, v in enumerate(row))
    return {"vectors": vectors, "score_31": score31, "wdl_before": p,
            "wdl_after": q, "goals_before": before, "goals_after": after,
            "market_wdl": m, "parameters_unvalidated": True,
            "market_provenance": ({"source": market["source"],
                                   "available_at": market["available_at"],
                                   "event_space": market["event_space"],
                                   "odds_type": market["odds_type"]}
                                  if weight > 0 else None)}
