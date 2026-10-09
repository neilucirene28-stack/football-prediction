"""BD-1 的独立 L3 赛事族比分先验，仅供影子验证。

不调用竞彩 predict()、校准文件或市场赔率。所有参数仅用 asof 前已可得
的常规时间结果估计。少于最低历史场数则拒绝输出，不能把规则先验冒充
已经通过 Brier 验证的生产模型。
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

from .snapshot import _datetime, _SCORE_HOME, _SCORE_DRAW, _SCORE_AWAY
from .math_core import handicap_1x2, match_probs, poisson_pmf


SHADOW_MODEL_VERSION = "bd1-l3-shadow-0.2"


def _history(rows: list[dict], asof: datetime) -> list[dict]:
    valid = []
    for row in rows:
        if row.get("regular_time") is not True:
            continue
        kickoff = _datetime(row["kickoff_at"], "kickoff_at")
        if kickoff >= asof:
            continue
        # 旧赛果若没有原始发布时间，只能从本系统真实首次核验时刻起使用。
        # 两个时间均有证据时取较晚者，绝不把当前核验倒填为历史赛前可得。
        stamps = [_datetime(row[key], key) for key in ("result_available_at", "verified_at")
                  if row.get(key) is not None]
        if not stamps:
            continue
        if any(stamp < kickoff for stamp in stamps):
            raise ValueError("赛果可用/核验时间早于开球")
        if max(stamps) > asof:
            continue
        if any(not isinstance(row.get(k), int) or isinstance(row.get(k), bool)
               or row[k] < 0 for k in ("ft_home", "ft_away", "ht_home", "ht_away")):
            raise ValueError("历史比分或半场比分非法")
        if row["ht_home"] > row["ft_home"] or row["ht_away"] > row["ft_away"]:
            raise ValueError("半场进球超过全场进球")
        valid.append(row)
    ids = [r["match_id"] for r in valid]
    if len(ids) != len(set(ids)):
        raise ValueError("历史比赛ID重复")
    return valid


def _root_matrix(rows: list[dict], tolerance: float = 1e-8) -> list[list[float]]:
    n = len(rows)
    # Jeffreys 平滑防止小样本零进球让整个维度退化；超参数待滚动选择。
    lam_h = (sum(r["ft_home"] for r in rows) + .5) / n
    lam_a = (sum(r["ft_away"] for r in rows) + .5) / n
    bound = max(10, max(max(r["ft_home"], r["ft_away"]) for r in rows))
    return _poisson_matrix(lam_h, lam_a, bound, tolerance)


def _poisson_matrix(lam_h: float, lam_a: float, bound: int = 10,
                    tolerance: float = 1e-8) -> list[list[float]]:
    if not all(math.isfinite(x) and 0 < x <= 8 for x in (lam_h, lam_a)):
        raise ValueError("进球强度超出影子模型数值预算")
    while True:
        ph = [poisson_pmf(i, lam_h) for i in range(bound + 1)]
        pa = [poisson_pmf(i, lam_a) for i in range(bound + 1)]
        if (1 - sum(ph)) + (1 - sum(pa)) < tolerance:
            break
        bound += 5
        if bound > 80:
            raise ValueError("比分尾部超过数值预算")
    m = [[h * a for a in pa] for h in ph]
    total = sum(map(sum, m))
    return [[v / total for v in row] for row in m]


def _family_matrix(rows: list[dict], family: str | None, root: list[list[float]],
                   kappa: float) -> tuple[list[list[float]], int]:
    if family is None:
        return root, 0
    selected = [r for r in rows if r.get("competition_family") == family]
    count = len(selected)
    n = len(root)
    m = [[kappa * root[i][j] for j in range(n)] for i in range(n)]
    for r in selected:
        h, a = r["ft_home"], r["ft_away"]
        if h >= n or a >= n:
            raise ValueError("历史比分超出先验支持集")
        m[h][a] += 1
    return [[p / (count + kappa) for p in row] for row in m], count


def _binomial(n: int, k: int, q: float) -> float:
    return math.comb(n, k) * q ** k * (1 - q) ** (n - k)


def _half_full(matrix: list[list[float]], qh: float, qa: float) -> dict[str, float]:
    keys = tuple(a + b for a in ("胜", "平", "负") for b in ("胜", "平", "负"))
    out = {k: 0.0 for k in keys}
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if p == 0:
                continue
            ft = "胜" if i > j else ("平" if i == j else "负")
            for h in range(i + 1):
                bh = _binomial(i, h, qh)
                for a in range(j + 1):
                    ht = "胜" if h > a else ("平" if h == a else "负")
                    out[ht + ft] += p * bh * _binomial(j, a, qa)
    return out


def _vectors_from_matrix(matrix: list[list[float]], qh: float, qa: float,
                         handicap: int | None) -> tuple[dict, dict]:
    """All six markets are derived from the same joint score distribution."""
    wdl = dict(zip(("胜", "平", "负"), match_probs(matrix)))
    handicap_wdl = (dict(zip(("胜", "平", "负"), handicap_1x2(matrix, handicap)))
                    if handicap is not None else None)
    score = {f"{i}-{j}": p for i, row in enumerate(matrix) for j, p in enumerate(row)}
    total = {str(i): 0.0 for i in range(7)}
    total["7+"] = 0.0
    odd_even = {k: 0.0 for k in ("上单", "上双", "下单", "下双")}
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            t = i + j
            total[str(t) if t < 7 else "7+"] += p
            odd_even[("上" if t >= 3 else "下") + ("单" if t % 2 else "双")] += p
    score31 = {k: score.get(k, 0.0) for k in (*_SCORE_HOME, *_SCORE_DRAW, *_SCORE_AWAY)}
    score31.update({"胜其他": 0.0, "平其他": 0.0, "负其他": 0.0})
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if f"{i}-{j}" not in score31:
                score31["胜其他" if i > j else ("平其他" if i == j else "负其他")] += p
    return ({"wdl": wdl, "handicap_wdl": handicap_wdl, "score": score,
             "total_goals": total, "half_full": _half_full(matrix, qh, qa),
             "odd_even": odd_even}, score31)


def predict_l3(history: list[dict], *, asof_at: str, kickoff_at: str,
               competition_family: str | None, handicap: int | None,
               kappa: float = 50.0, min_history: int = 30) -> dict:
    """赛事族 L3 冷启动影子预测。kappa/min_history 是待验证超参数。"""
    asof = _datetime(asof_at, "asof_at")
    if asof >= _datetime(kickoff_at, "kickoff_at"):
        raise ValueError("预测时点必须早于开球")
    if competition_family is not None and (
        not isinstance(competition_family, str) or not competition_family
    ):
        raise ValueError("赛事族必须是已核验名称或 null")
    if handicap is not None and (not isinstance(handicap, int) or isinstance(handicap, bool)):
        raise ValueError("北单让球线必须为整数或 null")
    if not isinstance(kappa, (int, float)) or not math.isfinite(kappa) or kappa <= 0:
        raise ValueError("kappa 必须大于零")
    if not isinstance(min_history, int) or isinstance(min_history, bool) or min_history < 1:
        raise ValueError("min_history 必须为正整数")
    rows = _history(history, asof)
    if len(rows) < min_history:
        raise ValueError(f"可用历史仅{len(rows)}场，低于影子基线最低{min_history}场")
    total_h = sum(r["ft_home"] for r in rows)
    total_a = sum(r["ft_away"] for r in rows)
    if total_h + total_a == 0:
        raise ValueError("历史总进球为零，无法拟合半场比例")
    qh = (sum(r["ht_home"] for r in rows) + .5) / (total_h + 1)
    qa = (sum(r["ht_away"] for r in rows) + .5) / (total_a + 1)
    root = _root_matrix(rows)
    matrix, family_n = _family_matrix(rows, competition_family, root, kappa)
    vectors, score31 = _vectors_from_matrix(matrix, qh, qa, handicap)
    return {
        "status": "shadow", "model_version": SHADOW_MODEL_VERSION,
        "route": "L3_prior_only", "parameters_unvalidated": True,
        "family_status": ("verified_family_input" if competition_family is not None
                          else "unknown_root_fallback"),
        "asof_at": asof_at, "kickoff_at": kickoff_at,
        "competition_family": competition_family,
        "training_n": len(rows), "family_n": family_n,
        "training_match_ids": [r["match_id"] for r in rows],
        "kappa": kappa, "ht_fractions": {"home": qh, "away": qa},
        "lambda_home": None, "lambda_away": None,
        "handicap": handicap,
        "vectors": vectors,
        "score_31": score31,
    }
