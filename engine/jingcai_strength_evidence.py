"""联赛统计及历史对手强度的赛前时间、原生身份与来源门控。"""
import re

from .jingcai_inputs import aware_datetime, finite_number

LEAGUE_SCHEMA = 'jingcai.league-statistics.v1'
REVISION = 'jingcai-strength-evidence-v13'


def _integer(value, path, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f'{path} 必须为不小于{minimum}的整数')
    return value


def _evidence_times(block, path, cutoff):
    stamps = {}
    for field in ('available_at', 'feature_as_of', 'collected_at'):
        if block.get(field) is None and field != 'collected_at':
            raise ValueError(f'{path}.{field} 缺失，不能证明数据时间范围')
        if block.get(field) is not None:
            stamps[field] = aware_datetime(block[field], f'{path}.{field}')
            if stamps[field] > cutoff:
                raise ValueError(f'{path}.{field} 晚于快照截止，存在未来泄漏')
    if stamps['feature_as_of'] > stamps['available_at']:
        raise ValueError(f'{path}.feature_as_of 晚于available_at')
    return stamps


def validate_league_statistics(payload, cutoff):
    block = payload.get('league_statistics')
    if block is None:
        return None
    if not isinstance(block, dict) or block.get('schema') != LEAGUE_SCHEMA:
        raise ValueError('league_statistics.schema 无效')
    b = dict(block)
    _evidence_times(b, 'league_statistics', cutoff)
    for key in ('source', 'competition_id', 'season_id', 'phase_id'):
        if not isinstance(b.get(key), str) or not b[key].strip():
            raise ValueError(f'league_statistics.{key} 必须为明确的原生来源标识')
    for key in ('match_count', 'home_goals', 'away_goals'):
        b[key] = _integer(b.get(key), f'league_statistics.{key}', 1 if key == 'match_count' else 0)
    if (not isinstance(b.get('evidence_sha256'), str) or
            not re.fullmatch(r'[a-f0-9]{64}', b['evidence_sha256'])):
        raise ValueError('league_statistics.evidence_sha256 必须为64位字节摘要')
    return b


def resolve_league_baseline(payload, input_audit, min_matches=30, *, enabled=True):
    default = payload['league_avg_goals']
    audit = {'value': default, 'status': input_audit['league_avg_goals_input_kind'],
             'provenance_status': 'caller_declared_not_source_authenticated',
             'minimum_matches': min_matches}
    stats = payload.get('league_statistics')
    if stats is None:
        return default, audit
    target = payload.get('match_source_context') or {}
    keys = ('source', 'competition_id', 'season_id', 'phase_id')
    if (not isinstance(target, dict) or
            any(not target.get(k) for k in keys) or
            any(target.get(k) != stats.get(k) for k in keys)):
        audit.update(statistics_status='excluded', reason='unbound_or_mismatched_native_league_season_phase')
        return default, audit
    if stats.get('sample_scope') != 'all_completed_league_fixtures' or stats.get('coverage_status') != 'complete_as_of':
        audit.update(statistics_status='excluded', reason='league_population_coverage_unverified')
        return default, audit
    if stats['match_count'] < min_matches:
        audit.update(statistics_status='excluded', reason='insufficient_league_match_count', match_count=stats['match_count'])
        return default, audit
    mean = (stats['home_goals'] + stats['away_goals']) / stats['match_count']
    if mean <= 0:
        audit.update(statistics_status='excluded', reason='nonpositive_observed_league_mean')
        return default, audit
    if not enabled:
        audit.update(statistics_status='eligible_not_enabled', observed_value=mean,
                     reason='normalized_baseline_candidate_not_enabled')
        return default, audit
    if input_audit['league_avg_goals_input_kind'] == 'declared_scalar_unverified' and abs(default - mean) > 1e-9:
        raise ValueError('league_avg_goals 与league_statistics不一致，请消除双重基线')
    audit.update(value=mean, status='observed_league_statistics', statistics_status='accepted',
                 match_count=stats['match_count'], source=stats['source'],
                 competition_id=stats['competition_id'], season_id=stats['season_id'],
                 phase_id=stats.get('phase_id'), evidence_sha256=stats['evidence_sha256'],
                 feature_as_of=stats['feature_as_of'], available_at=stats['available_at'])
    return mean, audit


def normalize_opponent_strength(row, path, cutoff, played_at):
    # 内部接受标记不信任调用方；裸数值只记录缺失证据，不进入修正。
    row.pop('_opponent_strength_validated', None)
    bare = any(k in row for k in ('opp_attack', 'opp_defense'))
    row.pop('opp_attack', None)
    row.pop('opp_defense', None)
    block = row.get('opponent_strength')
    if block is None:
        return {'status': 'excluded' if bare else 'missing',
                'reason': 'bare_ratings_without_identity_or_asof' if bare else 'no_opponent_strength'}
    if not isinstance(block, dict):
        raise ValueError(f'{path}.opponent_strength 必须为对象')
    b = dict(block)
    stamps = _evidence_times(b, f'{path}.opponent_strength', cutoff)
    attack = finite_number(b.get('attack'), f'{path}.opponent_strength.attack', minimum=0, strict_min=True)
    defense = finite_number(b.get('defense'), f'{path}.opponent_strength.defense', minimum=0, strict_min=True)
    _integer(b.get('sample_matches'), f'{path}.opponent_strength.sample_matches', 1)
    if played_at is None:
        return {'status': 'excluded', 'reason': 'historical_match_time_unknown'}
    if stamps['feature_as_of'] >= played_at:
        raise ValueError(f'{path}.opponent_strength.feature_as_of 必须早于历史比赛，不能使用赛后对手评级')
    if (not row.get('source') or not row.get('opponent_id') or
            row['source'] != b.get('source') or row['opponent_id'] != b.get('team_id')):
        return {'status': 'excluded', 'reason': 'native_opponent_identity_unbound_or_mismatched'}
    if b.get('scale') != 'relative_to_league_venue_baseline':
        return {'status': 'excluded', 'reason': 'opponent_rating_scale_unknown'}
    if b.get('sample_scope') != 'strictly_before_historical_match':
        return {'status': 'excluded', 'reason': 'historical_feature_window_unverified'}
    row['opponent_strength'] = b
    row['opp_attack'], row['opp_defense'] = attack, defense
    row['_opponent_strength_validated'] = True
    return {'status': 'accepted', 'source': b['source'], 'team_id': b['team_id'],
            'feature_as_of': b['feature_as_of'],
            'provenance_status': 'caller_declared_not_source_authenticated'}
