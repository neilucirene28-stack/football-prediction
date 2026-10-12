"""竞彩解释层：不修改比分分布；启发式数据质量分不代表命中率。"""

def lineup_confirmation(audit):
    observations = audit.get('team_observations') or {}
    legacy = (audit.get('team_intelligence') or {}).get('teams') or {}
    sides = {}
    for side in ('home', 'away'):
        chosen = (observations.get('selected_by_dimension', {}).get(side) or {}).get('lineup')
        fallback = legacy.get(side)
        statuses = [x.get('lineup_status', 'unknown') for x in (chosen, fallback) if x]
        status = ('confirmed' if statuses and all(s == 'confirmed' for s in statuses)
                  else 'expected' if 'expected' in statuses else 'unknown')
        if any(c.get('side') == side for c in observations.get('conflicts', [])):
            status = 'unknown'
        sides[side] = {'status': status,
                       'source': chosen.get('source') if chosen else 'team_intelligence' if fallback else None,
                       'status_conflict': len(set(statuses)) > 1}
    return {'sides': sides, 'both_confirmed': all(s['status'] == 'confirmed' for s in sides.values()),
            'policy': 'validated_selected_lineups_only; injury_presence_is_not_confirmation'}


def score_explanation(matrix, probabilities, n=5):
    direction = max(range(3), key=lambda i: probabilities[i])
    cells = [(h, a, float(p)) for h, row in enumerate(matrix) for a, p in enumerate(row)]
    winner = lambda h, a: 0 if h > a else 1 if h == a else 2
    ranked = sorted(cells, key=lambda x: (-x[2], x[0], x[1]))
    conditional = [x for x in ranked if winner(x[0], x[1]) == direction][:n]
    fmt = lambda x: {'score': f'{x[0]}-{x[1]}', 'prob': x[2]}
    mode = ranked[0]
    return {'wdl_direction': ('home', 'draw', 'away')[direction],
            'wdl_probability': float(probabilities[direction]), 'global_mode': fmt(mode),
            'global_top2': [fmt(x) for x in ranked[:2]],
            'global_top2_coverage': sum(x[2] for x in ranked[:2]),
            'global_top2_rule': 'unconditional_probability_descending; no_diversity_override',
            'global_mode_direction_differs': winner(mode[0], mode[1]) != direction,
            'scores_within_wdl_direction': [fmt(x) for x in conditional],
            'qualification': 'score probabilities are unconditional; a single modal score may differ from aggregated WDL; not a distribution conflict'}
