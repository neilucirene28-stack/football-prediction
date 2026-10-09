"""北单独立链路用的最小比分概率聚合。"""
import math


def poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def match_probs(matrix) -> tuple[float, float, float]:
    home = draw = away = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:
                home += p
            elif i == j:
                draw += p
            else:
                away += p
    return home, draw, away


def handicap_1x2(matrix, line: int) -> tuple[float, float, float]:
    home = draw = away = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i + line > j:
                home += p
            elif i + line == j:
                draw += p
            else:
                away += p
    return home, draw, away
