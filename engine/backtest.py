"""复盘层：proper scoring rules 与校准。"""
import math


def brier_score(p: tuple[float, float, float], outcome: int) -> float:
    """outcome: 0=主胜 1=平 2=客胜。越小越好。"""
    o = [0.0, 0.0, 0.0]
    o[outcome] = 1.0
    return sum((pi - oi) ** 2 for pi, oi in zip(p, o)) / 3.0


def log_loss(p: tuple[float, float, float], outcome: int) -> float:
    return -math.log(max(p[outcome], 1e-12))


def ranked_probability_score(p: tuple[float, float, float], outcome: int) -> float:
    """RPS：足球预测标准评分（有序结果），越小越好。"""
    cum_p, cum_o, total = 0.0, 0.0, 0.0
    for i in range(3):
        cum_p += p[i]
        cum_o = 1.0 if i >= outcome else 0.0
        total += (cum_p - cum_o) ** 2
    return total / 2.0


def calibration_table(rows: list[tuple[tuple[float, float, float], int]],
                      bins: int = 10, outcome_index: int = 0) -> list[dict]:
    """One-vs-rest calibration; default remains home win for compatibility."""
    if bins < 1 or outcome_index not in (0, 1, 2):
        raise ValueError("bins must be positive and outcome_index must be 0, 1 or 2")
    buckets = [[] for _ in range(bins)]
    for p, outcome in rows:
        probability = p[outcome_index]
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("invalid calibration probability")
        buckets[min(int(probability * bins), bins - 1)].append((probability, int(outcome == outcome_index)))
    return [{"bin": f"{i / bins:.1f}-{(i + 1) / bins:.1f}", "n": len(bucket),
             "predicted": round(sum(p for p, _ in bucket) / len(bucket), 4),
             "actual": round(sum(y for _, y in bucket) / len(bucket), 4)}
            for i, bucket in enumerate(buckets) if bucket]


def calibration_by_class(rows, bins: int = 10) -> dict:
    return {name: calibration_table(rows, bins, i)
            for i, name in enumerate(("home", "draw", "away"))}


def _sigmoid(x: float) -> float:
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def platt_fit(probs: list[float], labels: list[int],
              iters: int = 100) -> tuple[float, float]:
    """Platt Scaling：拟合 P = sigmoid(A·logit(p) + B)，纠正系统性过度自信。

    probs: 模型对某结果的历史预测概率；labels: 0/1 实际是否发生。
    返回 (A, B)。样本 <50 时不建议使用。
    """
    def logit(p):
        p = min(max(p, 1e-6), 1 - 1e-6)
        return math.log(p / (1 - p))

    xs = [logit(p) for p in probs]
    a, b = 1.0, 0.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, labels):
            q = _sigmoid(a * x + b)
            err = q - y
            ga += err * x
            gb += err
            w = q * (1 - q)
            haa += w * x * x
            hab += w * x
            hbb += w
        det = haa * hbb - hab * hab
        if abs(det) < 1e-12:
            break
        da = (ga * hbb - gb * hab) / det
        db = (haa * gb - hab * ga) / det
        a -= da
        b -= db
        if abs(da) + abs(db) < 1e-9:
            break
    return a, b


def platt_apply(p: float, a: float, b: float) -> float:
    """对单个概率应用已拟合的 Platt 参数。"""
    p = min(max(p, 1e-6), 1 - 1e-6)
    x = math.log(p / (1 - p))
    return _sigmoid(a * x + b)
