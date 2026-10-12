"""竞彩进球强度；规范基线候选保持总均值，兼容模式保留v12数值策略。"""
from collections import Counter

REVISION = 'jingcai-venue-baselines-v13'
LEAGUE_TIERS = {
    '日职联': 1, '日职乙': 2, '日丙': 3, '日足联': 4,
    '日联杯': 0, '日皇杯': 0, '日本超级杯': 0,
    '英超': 1, '英冠': 2, '英甲': 3, '英乙': 4, '英非联': 5,
    '足总杯': 0, '英联杯': 0, '英锦标赛': 0,
    '西甲': 1, '西乙': 2, '西协甲': 3, '国王杯': 0,
    '德甲': 1, '德乙': 2, '德丙': 3, '德国杯': 0,
    '意甲': 1, '意乙': 2, '意丙': 3, '意大利杯': 0,
    '法甲': 1, '法乙': 2, '法国杯': 0,
    '荷甲': 1, '荷乙': 2, '葡超': 1, '苏超': 1, '土超': 1,
    '美职联': 1, '美职': 1, '巴甲': 1, '巴西甲': 1,
    '巴西乙': 2, '巴乙': 2, '巴西丙': 3, '巴丙': 3,
    '阿甲': 1, '阿职联': 1, 'K联赛': 1, '韩K联': 1,
    '中超': 1, '中甲': 2, '澳超': 1, '沙特联': 1,
    'J1': 1, '墨联': 1, '挪超': 1, '爱超': 1, '苏冠': 2,
    '欧国联': 0, '球会友谊': 0,
    '欧冠': 1, '欧联': 1, '欧协联': 1, '亚冠': 1, '亚冠精英': 1,
    '解放者杯': 1, '世俱杯': 1,
}


def team_tier(recent):
    # comp必须是上游解析出的独立赛事字段；不从带轮次的展示文本猜测。
    tiers = [LEAGUE_TIERS[r['comp']] for r in recent
             if LEAGUE_TIERS.get(r.get('comp'), 0) > 0]
    return Counter(tiers).most_common(1)[0][0] if tiers else None


def venue_baselines(total, home_adv_factor=1.12):
    if total <= 0 or home_adv_factor <= 0:
        raise ValueError('进球基线与主客场比例必须为正数')
    away = total / (1 + home_adv_factor)
    return away * home_adv_factor, away


def _attack_defense(recent, total, ratio, venue, decay, shrink_prior, normalized=True):
    home_base, away_base = venue_baselines(total, ratio)
    rows = []
    for i, row in enumerate(recent):
        v = row.get('venue')
        v = v if v in ('H', 'A', 'N') else 'N'
        if v in (venue, 'N'):
            rows.append((i, row, v))
    if not rows:
        return 1.0, 1.0, {'n': 0, 'effective_n': 0.0, 'opp_adjust_coverage': 0.0,
                          'neutral_or_unknown_n': 0}
    w_sum = attack_sum = defense_sum = 0.0
    covered = 0
    for i, r, v in rows:
        w = decay ** i
        expected_gf, expected_ga = ((home_base, away_base) if v == 'H' else
                                    (away_base, home_base) if v == 'A' else
                                    (total / 2, total / 2))
        gf, ga = r['gf'], r['ga']
        if r.get('_opponent_strength_validated') is True:
            covered += 1
            gf /= max(r['opp_defense'], 0.2)
            ga /= max(r['opp_attack'], 0.2)
        w_sum += w
        attack_sum += w * gf / expected_gf if normalized else w * gf
        defense_sum += w * ga / expected_ga if normalized else w * ga
    attack = max(0.2, attack_sum / w_sum if normalized else (attack_sum / w_sum) / (total / 2))
    defense = max(0.2, defense_sum / w_sum if normalized else (defense_sum / w_sum) / (total / 2))
    if shrink_prior > 0:
        attack = (w_sum * attack + shrink_prior) / (w_sum + shrink_prior)
        defense = (w_sum * defense + shrink_prior) / (w_sum + shrink_prior)
    return attack, defense, {
        'n': len(rows), 'effective_n': round(w_sum, 2),
        'opp_adjust_coverage': round(covered / len(rows), 3),
        'neutral_or_unknown_n': sum(v == 'N' for _, _, v in rows),
    }


def estimate_lambdas(home_recent, away_recent, league_avg_goals,
                     home_adv_factor=1.12, injury=None, decay=0.9,
                     home_tier=None, away_tier=None, shrink_prior=3.0,
                     baseline_mode='venue_normalized'):
    if baseline_mode not in ('legacy_v12', 'venue_normalized'):
        raise ValueError('未知竞彩进球基线模式')
    normalized = baseline_mode == 'venue_normalized'
    # 中立赛只改变本场进球基线；历史主客场评级仍用历史的1.12基线。
    home_base, away_base = venue_baselines(league_avg_goals, home_adv_factor)
    if not normalized:
        home_base, away_base = league_avg_goals / 2 * home_adv_factor, league_avg_goals / 2
    ah, dh, h = _attack_defense(home_recent, league_avg_goals, 1.12, 'H', decay, shrink_prior, normalized)
    aa, da, a = _attack_defense(away_recent, league_avg_goals, 1.12, 'A', decay, shrink_prior, normalized)
    tier = None
    if home_tier is not None and away_tier is not None and home_tier != away_tier:
        gap = home_tier - away_tier
        ah *= 0.85 ** gap
        dh *= 1.18 ** gap
        aa *= 0.85 ** -gap
        da *= 1.18 ** -gap
        tier = {'home_tier': home_tier, 'away_tier': away_tier, 'gap': gap}
    lam_h, lam_a = ah * da * home_base, aa * dh * away_base
    if not normalized:
        # 保留v12浮点运算顺序，兼容模式不因重排乘法改变原始矩阵。
        lam_h = ah * da * (league_avg_goals / 2) * home_adv_factor
    notes = {
        'attack_home': round(ah, 3), 'defense_home': round(dh, 3),
        'attack_away': round(aa, 3), 'defense_away': round(da, 3),
        'home_sample': h['n'], 'away_sample': a['n'],
        'home_effective_n': h['effective_n'], 'away_effective_n': a['effective_n'],
        'opp_adjust_coverage': min(h['opp_adjust_coverage'], a['opp_adjust_coverage']),
        'neutral_or_unknown_sample': {'home': h['neutral_or_unknown_n'], 'away': a['neutral_or_unknown_n']},
        'decay': decay, 'shrink_prior': shrink_prior,
        'degraded': h['n'] < 5 or a['n'] < 5,
        'strength_revision': REVISION,
        'baseline_mode': baseline_mode,
        'league_total_baseline': league_avg_goals,
        'home_goal_baseline': home_base, 'away_goal_baseline': away_base,
        'historical_home_away_ratio': 1.12 if normalized else 1.0,
        'baseline_total_before_team_strength': home_base + away_base,
        'home_adv_policy': ('split_total_baseline_once; venue_specific_history_normalization'
                            if normalized else 'legacy_v12_compatibility; normalized_candidate_not_promoted'),
    }
    if tier:
        notes['tier_adjust'] = tier
    if injury:
        for key, factor in injury.items():
            if key in ('home_attack', 'away_defense'):
                lam_h *= factor
            elif key in ('away_attack', 'home_defense'):
                lam_a *= factor
        notes['injury_applied'] = dict(injury)
    clipped_h = min(max(lam_h, 0.15), 4.5)
    clipped_a = min(max(lam_a, 0.10), 4.0)
    if (clipped_h, clipped_a) != (lam_h, lam_a):
        notes['lambda_bounds_applied'] = {'before': [lam_h, lam_a], 'after': [clipped_h, clipped_a]}
    return clipped_h, clipped_a, notes
