"""竞彩整数让球的校验和展示；不训练、不修改概率。"""
import math
import re

OUTCOMES = ("home", "draw", "away")
LABELS = {"home": "让胜", "draw": "让平", "away": "让负"}
PIPELINE_REVISION = "jingcai-cards-joint-ht-final-mc-20261010-v5"
# 固定先验来自 2026-09-01~30，不能用于这个训练期内的历史验证。
LETDRAW_FIT_BEFORE = "2026-10-01T00:00:00+08:00"


def integer_handicap(value):
    if isinstance(value, bool):
        raise ValueError("竞彩让球必须为整数，不能为布尔值")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value)
    raise ValueError("竞彩让球必须为官方整数，不能截断半球／四分之一球")


def handicap_view(probabilities, rq):
    rq = integer_handicap(rq)
    p = tuple(probabilities)
    if (len(p) != 3 or any(not math.isfinite(v) or v < 0 or v > 1 for v in p)
            or abs(sum(p) - 1) > 1e-8):
        raise ValueError("让球三项概率必须合法且加总为1")
    ranked = sorted(range(3), key=lambda i: (-p[i], i))
    top, second, excluded = ranked
    pair_draw = (top, 1) if top != 1 else (top, second)
    pair_excluded = next(i for i in range(3) if i not in pair_draw)
    margin = -rq
    text = (f"主队恰好赢{margin}球" if margin > 0 else
            f"主队恰好输{-margin}球" if margin < 0 else "主客打平")
    return {
        "probabilities_full": dict(zip(OUTCOMES, p)),
        "top1": OUTCOMES[top], "top1_probability": p[top],
        "draw_probability": p[1], "draw_margin": margin,
        "draw_interpretation": text,
        "top2": [OUTCOMES[top], OUTCOMES[second]],
        "top2_coverage": p[top] + p[second],
        "top2_excluded": OUTCOMES[excluded],
        "top2_excluded_probability": p[excluded],
        "pair_including_draw": [OUTCOMES[i] for i in pair_draw],
        "pair_including_draw_coverage": sum(p[i] for i in pair_draw),
        "pair_including_draw_excluded_probability": p[pair_excluded],
        "coverage_note": "覆盖率是互斥结果概率和，不是收益或实战命中保证",
    }


def matrix_summary(matrix):
    n = len(matrix)
    return {
        "mass": sum(sum(row) for row in matrix),
        "mean_home": sum(i * matrix[i][j] for i in range(n) for j in range(n)),
        "mean_away": sum(j * matrix[i][j] for i in range(n) for j in range(n)),
    }
