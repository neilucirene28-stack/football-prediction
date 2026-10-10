"""Experimental KL projection with fixed event masses and total-goal mean.

This numerical layer accepts a target, not odds or unaudited SP. It does not
estimate calibration, prove absence of statistical bias, or grant production
eligibility. Nonidentity shadow calibration uses this layer; defaults remain identity.
"""
from __future__ import annotations

import math
import re

from .baseline import _vectors_from_matrix

OUTCOMES = ("胜", "平", "负")


class ProjectionUnavailable(ValueError):
    """The requested constraints cannot safely be solved on existing support."""


def project_fixed_mean(score: dict[str, float], *, target: dict[str, float],
                       line: int, tolerance: float = 1e-10) -> dict:
    """Minimise KL(new||base), keeping total mean and handicap event masses.

    p'(h,a)=q[r] p(h,a) exp(beta*(h+a))/Z[r](beta).
    A scalar bracketed solve enforces E'[H+A]=E[H+A]. Zero cells stay zero.
    Infeasibility is explicit; neither the target nor the mean is relaxed.
    """
    if not isinstance(line, int) or isinstance(line, bool) or abs(line) > 80:
        raise ValueError("让球线必须是支持范围内的整数")
    if (not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool)
            or not math.isfinite(tolerance) or not 1e-13 <= tolerance <= 1e-8):
        raise ValueError("数值容差无效")
    if (not isinstance(target, dict) or set(target) != set(OUTCOMES)
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) or v <= 0 for v in target.values())
            or abs(math.fsum(target.values()) - 1) > 1e-12):
        raise ValueError("目标须为三个正概率且总和为1")
    if not isinstance(score, dict) or not score:
        raise ValueError("基础比分为空")
    cells, groups = [], {k: [] for k in OUTCOMES}
    for label, p in score.items():
        if not isinstance(label, str) or not re.fullmatch(r"(0|[1-9]\d*)-(0|[1-9]\d*)", label):
            raise ValueError("比分标签无效")
        h, a = map(int, label.split("-"))
        if (max(h, a) > 80 or isinstance(p, bool) or not isinstance(p, (int, float))
                or not math.isfinite(p) or p < 0):
            raise ValueError("基础比分概率或支持集无效")
        k = "胜" if h + line > a else "平" if h + line == a else "负"
        cell = (label, h + a, p)
        cells.append(cell)
        if p > 0:
            groups[k].append((label, h + a, math.log(p)))
    if abs(math.fsum(p for _, _, p in cells) - 1) > 1e-12:
        raise ValueError("基础比分概率总和不为1")
    if any(not g for g in groups.values()):
        raise ProjectionUnavailable("目标事件在原支持集上没有正概率")
    mean = math.fsum(t * p for _, t, p in cells)
    lower = math.fsum(target[k] * min(t for _, t, _ in groups[k]) for k in OUTCOMES)
    upper = math.fsum(target[k] * max(t for _, t, _ in groups[k]) for k in OUTCOMES)
    if mean < lower - tolerance or mean > upper + tolerance:
        raise ProjectionUnavailable(f"固定均值{mean:.9f}不在目标可行区间[{lower:.9f},{upper:.9f}]")

    def distribution(beta):
        out = dict.fromkeys(score, 0.0)
        for k, g in groups.items():
            logs = [logp + beta * t for _, t, logp in g]
            peak = max(logs)
            weights = [math.exp(x - peak) for x in logs]
            z = math.fsum(weights)
            for (label, _, _), w in zip(g, weights):
                out[label] = target[k] * w / z
        return out, math.fsum(t * out[label] for label, t, _ in cells)

    # Preserve the exact identity, rather than adding rounding to every run.
    base_mass = {k: math.fsum(math.exp(lp) for _, _, lp in g) for k, g in groups.items()}
    if max(abs(base_mass[k] - target[k]) for k in OUTCOMES) <= tolerance:
        return {"score": dict(score), "beta": 0.0, "mean_before": mean,
                "mean_after": mean, "iterations": 0, "feasible_bounds": [lower, upper]}
    candidate, after = distribution(0.)
    beta, iterations = 0., 0
    if abs(after - mean) > tolerance:
        lo, hi = -1., 1.
        for _ in range(7):
            if distribution(lo)[1] <= mean + tolerance:
                break
            lo *= 2
        for _ in range(7):
            if distribution(hi)[1] >= mean - tolerance:
                break
            hi *= 2
        if distribution(lo)[1] > mean + tolerance or distribution(hi)[1] < mean - tolerance:
            raise ProjectionUnavailable("无法在数值预算内括住固定均值解")
        for iterations in range(1, 101):
            beta = (lo + hi) / 2
            candidate, after = distribution(beta)
            if abs(after - mean) <= tolerance:
                break
            if after < mean:
                lo = beta
            else:
                hi = beta
        else:
            raise ProjectionUnavailable("固定均值数值求解未收敛")
    if abs(math.fsum(candidate.values()) - 1) > 1e-12 or abs(after - mean) > tolerance:
        raise ProjectionUnavailable("求解后概率或均值残差超限")
    return {"score": candidate, "beta": beta, "mean_before": mean,
            "mean_after": after, "iterations": iterations, "feasible_bounds": [lower, upper]}


def shadow_fixed_mean(prediction: dict, *, target: dict[str, float], line: int) -> dict:
    """Rebuild all six markets, or explicitly retain the unchanged prior.

    The target belongs to sign(H+line-A), even when line != 0. It must never
    be silently treated as unhandicapped WDL. Total mean is preserved; home
    and away marginal means can change. This is an unaudited shadow candidate.
    """
    if prediction.get("handicap") != line:
        raise ValueError("目标事件与基础预测整数让球线不一致")
    base = prediction["vectors"]["score"]
    try:
        fit = project_fixed_mean(base, target=target, line=line)
    except ProjectionUnavailable as exc:
        mean = math.fsum(sum(map(int, k.split("-"))) * v for k, v in base.items())
        return {"vectors": prediction["vectors"], "score_31": prediction["score_31"],
                "status": "fallback_prior", "reason": str(exc), "mean_before": mean,
                "mean_after": mean, "target_applied": False, "parameters_unvalidated": True,
                "production_eligible": False, "beta": None}
    n = max(max(map(int, k.split("-"))) for k in fit["score"]) + 1
    matrix = [[fit["score"].get(f"{h}-{a}", 0.) for a in range(n)] for h in range(n)]
    fractions = prediction["ht_fractions"]
    if any(not math.isfinite(fractions[k]) or not 0 < fractions[k] < 1 for k in ("home", "away")):
        raise ValueError("半场进球比例无效")
    vectors, score31 = _vectors_from_matrix(matrix, fractions["home"], fractions["away"], line)
    if any(abs(vectors["handicap_wdl"][k] - target[k]) > 1e-9 for k in OUTCOMES):
        raise ProjectionUnavailable("衍生让球概率残差超限")
    return {**fit, "vectors": vectors, "score_31": score31, "status": "projected_shadow",
            "reason": None, "target_applied": True, "parameters_unvalidated": True,
            "production_eligible": False}
