"""竞彩候选：市场条件化比分与保留胜平负的整数让球融合。

无训练、无联网、无自动晋级。所有返回矩阵均为副本。
"""
import math

from .jingcai_grid import score_grid, validate_dc
from .jingcai_handicap import integer_handicap
from .poisson import handicap_1x2, match_probs, score_matrix

REVISION = "jingcai-joint-wdl-handicap-score-v14"


def probabilities(values):
    if len(values) != 3 or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("三向概率无效")
    total = sum(values)
    if total <= 0:
        raise ValueError("三向概率和必须为正")
    return tuple(v / total for v in values)


def market_weights(old, market_weight, has_market):
    if market_weight is None or not has_market:
        return dict(old)
    if not math.isfinite(market_weight) or not 0 <= market_weight <= 1:
        raise ValueError("市场权重须在0到1之间")
    other = old['model'] + old['elo']
    if other <= 0:
        return {'model': 0., 'market': 1., 'elo': 0.}
    return {'model': (1-market_weight)*old['model']/other,
            'market': market_weight, 'elo': (1-market_weight)*old['elo']/other}


def goal_mean_from_market(over, under, line=2.5):
    """仅支持无走盘的2.5球盘口；不把整数/四分之一球直接二分。"""
    if line != 2.5 or any(not math.isfinite(x) or x <= 1 for x in (over, under)):
        raise ValueError("总进球市场须为2.5球且两向十进制赔率大于1")
    target_under = (1/under)/(1/under+1/over)
    lo, hi = .02, 15.
    for _ in range(60):
        mu = (lo+hi)/2
        cdf = math.exp(-mu)*(1+mu+mu*mu/2)
        if cdf > target_under:
            lo = mu
        else:
            hi = mu
    return (lo+hi)/2


def _wdl_fast(lh, la, rho):
    # 归一化共同因子在主胜/(主胜+客胜)中抵消；支持足够宽的固定网格。
    h, a = [math.exp(-lh)], [math.exp(-la)]
    for i in range(1, 31):
        h.append(h[-1]*lh/i); a.append(a[-1]*la/i)
    ph = pa = ch = ca = 0.
    for i in range(31):
        ph += h[i]*ca; pa += a[i]*ch
        ch += h[i]; ca += a[i]
    low = h[0]*a[0]*lh*la*rho
    return ph+low, pa+low


def conditioned_score_matrix(lh, la, target_wdl, rho=-.13,
                             goals_market=None, total_market_weight=0.):
    target = probabilities(target_wdl)
    requested_total = lh+la
    market_mean = None
    if goals_market is not None and total_market_weight > 0:
        market_mean = goal_mean_from_market(goals_market['over'], goals_market['under'],
                                           goals_market['line'])
        requested_total = (1-total_market_weight)*(lh+la)+total_market_weight*market_mean
    if not 0 <= total_market_weight <= 1:
        raise ValueError("总进球市场权重须在0到1之间")
    dc_max = -1/rho*(1-1e-10) if rho < 0 else 100.
    max_h, max_a = min(4.5, dc_max), min(4., dc_max)
    total = min(max(requested_total, .2), max_h+max_a)
    lo, hi = max(.05, total-max_a), min(max_h, total-.05)
    desired = target[0]/max(target[0]+target[2], 1e-15)
    for _ in range(45):
        home = (lo+hi)/2
        ph, pa = _wdl_fast(home, total-home, rho)
        if ph/max(ph+pa, 1e-15) < desired:
            lo = home
        else:
            hi = home
    nh = (lo+hi)/2; na = total-nh
    validate_dc(nh, na, rho)
    upper, tail = score_grid(nh, na)
    matrix = score_matrix(nh, na, rho=rho, max_goals=upper)
    return matrix, {'status': 'applied', 'revision': REVISION,
        'raw_form_lambdas': [lh, la], 'effective_score_lambdas': [nh, na],
        'total_market_mean': market_mean, 'requested_total_mean': requested_total,
        'total_clipped_to_rate_support': total != requested_total,
        'total_market_weight_applied': total_market_weight if market_mean is not None else 0.,
        'target_wdl': list(target), 'before_wdl_projection': list(match_probs(matrix)),
        'max_goals': upper, 'poisson_tail_mass': tail,
        'policy': 'fit_home_away_ratio_then_project_exact_wdl; no_argmax_override'}


def fuse_handicap_fixed_wdl(matrix, line, market, weight):
    """混合让球目标后，投影到与已确定胜平负兼容的可行集合。

    整数±1的让负/让胜有固定边际；不能为命中让平强行改变胜平负。
    对KL(q||混合目标)求解带下界的三类单纯形，并只缩放受影响WDL区域。
    """
    line = integer_handicap(line)
    target = probabilities(market)
    if not math.isfinite(weight) or not 0 <= weight <= 1:
        raise ValueError("让球市场权重须在0到1之间")
    old = list(handicap_1x2(matrix, line))
    wdl = match_probs(matrix)
    copied = [list(row) for row in matrix]
    if weight == 0 or line == 0:
        return copied, {'status': 'unchanged', 'reason': 'zero_weight_or_zero_line',
                        'before': old, 'after': old}
    region = 0 if line < 0 else 2
    cells, free, fixed = [], [0., 0., 0.], [0., 0., 0.]
    for h, row in enumerate(matrix):
        for a, p in enumerate(row):
            w = 0 if h > a else 1 if h == a else 2
            k = 0 if h-a+line > 0 else 1 if h-a+line == 0 else 2
            if w == region:
                free[k] += p; cells.append((h, a, k))
            else:
                fixed[k] += p
    mixed = [(1-weight)*p+weight*q for p, q in zip(old, target)]
    active = [i for i in range(3) if free[i] > 0]
    if not active or wdl[region] <= 0:
        return copied, {'status': 'unchanged', 'reason': 'no_free_support',
                        'before': old, 'after': old}
    # 非活动项严格固定。活动项下界为其他胜负区域贡献的固定概率。
    lo, hi = 0., 1.
    wanted = 1-sum(fixed[i] for i in range(3) if i not in active)
    positive = [max(mixed[i], 1e-15) for i in range(3)]
    def mass(scale):
        return sum(max(fixed[i], scale*positive[i]) for i in active)
    while mass(hi) < wanted:
        hi *= 2
    for _ in range(65):
        mid = (lo+hi)/2
        if mass(mid) < wanted:
            lo = mid
        else:
            hi = mid
    projected = list(fixed)
    for i in active:
        projected[i] = max(fixed[i], (lo+hi)/2*positive[i])
    desired_free = [max(projected[i]-fixed[i], 0.) for i in range(3)]
    scale = wdl[region]/sum(desired_free)
    desired_free = [p*scale for p in desired_free]
    for h, a, k in cells:
        copied[h][a] = matrix[h][a]*desired_free[k]/free[k] if free[k] > 0 else 0.
    return copied, {'status': 'applied', 'revision': REVISION, 'weight': weight,
        'line': line, 'market_target': list(target), 'mixed_target': mixed,
        'feasible_target': list(handicap_1x2(copied, line)),
        'before': old, 'after': list(handicap_1x2(copied, line)),
        'fixed_lower_bounds': fixed, 'unchanged_wdl': list(wdl),
        'target_projected_for_coherence': max(abs(x-y) for x,y in
            zip(mixed, handicap_1x2(copied, line))) > 1e-9,
        'policy': 'same_final_score_matrix; fixed_wdl; no_forced_handicap_draw'}
