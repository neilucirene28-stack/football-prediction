"""数学不变量与来源门控；合成用例不作为准确率证据。"""
from copy import deepcopy

import pytest

from engine.jingcai_predictor import predict, PredictError
from engine.jingcai_strengths import estimate_lambdas, venue_baselines
from engine.jingcai_strength_evidence import LEAGUE_SCHEMA
from test_jingcai_inputs_grid import payload, CONFIG as BASE_CONFIG, ASOF

CONFIG = {**BASE_CONFIG, 'strength_baseline_mode': 'venue_normalized'}


def context_payload():
    p = payload()
    p['match_source_context'] = {'source': 'synthetic', 'competition_id': 'league-1',
                                 'season_id': '2029-30', 'phase_id': 'regular'}
    p['league_statistics'] = {
        **p['match_source_context'], 'schema': LEAGUE_SCHEMA,
        'sample_scope': 'all_completed_league_fixtures', 'coverage_status': 'complete_as_of',
        'match_count': 100, 'home_goals': 170, 'away_goals': 130,
        'evidence_sha256': 'a' * 64, 'available_at': p['snapshot_at'],
        'feature_as_of': '2029-12-31T23:00:00+08:00',
    }
    return p


def opponent_payload():
    p = payload()
    for side, v in [('home', 'H'), ('away', 'A')]:
        p[side + '_recent'] = [{
            'gf': 2, 'ga': 1, 'venue': v, 'source': 'synthetic', 'opponent_id': 'opp',
            'played_at': '2029-12-25T20:00:00+08:00',
            'available_at': p['snapshot_at'],
        } for _ in range(8)]
    p['home_recent'][0]['opponent_strength'] = {
        'source': 'synthetic', 'team_id': 'opp', 'attack': 2, 'defense': 2,
        'sample_matches': 10, 'scale': 'relative_to_league_venue_baseline',
        'sample_scope': 'strictly_before_historical_match',
        'feature_as_of': '2029-12-24T23:00:00+08:00', 'available_at': p['snapshot_at'],
    }
    return p


@pytest.mark.parametrize('mean', [1.8, 2.7, 3.4, 4.1])
@pytest.mark.parametrize('ratio', [1.0, 1.12, 1.3])
def test_baselines_preserve_total_and_ratio(mean, ratio):
    h, a = venue_baselines(mean, ratio)
    assert h + a == pytest.approx(mean)
    assert h / a == pytest.approx(ratio)


def test_no_history_returns_prior_without_inflating_total():
    h, a, notes = estimate_lambdas([], [], 2.7)
    assert h + a == pytest.approx(2.7)
    assert notes['home_sample'] == notes['away_sample'] == 0
    assert notes['degraded']


def test_venue_observations_do_not_receive_home_advantage_twice():
    hs = [{'gf': 2, 'ga': 1, 'venue': 'H'}] * 8
    aws = [{'gf': 1, 'ga': 2, 'venue': 'A'}] * 8
    hb, ab = venue_baselines(2.7)
    h, a, _ = estimate_lambdas(hs, aws, 2.7, decay=1, shrink_prior=0)
    assert h == pytest.approx(4 / hb)
    assert a == pytest.approx(1 / ab)


def test_wrong_venue_excluded_unknown_remains_explicitly_diagnosed():
    hs = [{'gf': 2, 'ga': 1, 'venue': 'H'}, {'gf': 100, 'ga': 100, 'venue': 'A'}]
    aws = [{'gf': 1, 'ga': 1}]
    h, a, notes = estimate_lambdas(hs, aws, 2.7)
    assert notes['home_sample'] == notes['away_sample'] == 1
    assert notes['neutral_or_unknown_sample'] == {'home': 0, 'away': 1}
    assert 0 < h < 4.5 and 0 < a < 4


def test_lambda_bounds_are_audited():
    rows = [{'gf': 100, 'ga': 100}] * 8
    h, a, notes = estimate_lambdas(rows, rows, 2.7)
    assert (h, a) == (4.5, 4.0)
    assert notes['lambda_bounds_applied']['after'] == [h, a]


def test_observed_league_mean_used_with_native_scope_and_population_coverage():
    p = context_payload(); before = deepcopy(p)
    result = predict(p, CONFIG, asof=ASOF)
    audit = result['input_audit']['league_baseline']
    assert audit['status'] == 'observed_league_statistics'
    assert audit['value'] == 3
    assert result['lambda_notes']['league_total_baseline'] == 3
    assert p == before


@pytest.mark.parametrize('key', ['source', 'competition_id', 'season_id', 'phase_id'])
def test_cross_source_season_or_phase_statistics_not_applied(key):
    p = context_payload(); p['league_statistics'][key] = 'other'
    result = predict(p, CONFIG, asof=ASOF)
    assert result['input_audit']['league_baseline']['statistics_status'] == 'excluded'
    assert result['lambda_notes']['league_total_baseline'] == 2.7


@pytest.mark.parametrize('field,value', [('sample_scope', 'selected_team_recent'),
                                        ('coverage_status', 'partial')])
def test_selected_team_samples_cannot_be_called_league_mean(field, value):
    p = context_payload(); p['league_statistics'][field] = value
    result = predict(p, CONFIG, asof=ASOF)
    assert result['input_audit']['league_baseline']['reason'] == 'league_population_coverage_unverified'


def test_small_league_sample_not_activated():
    p = context_payload(); p['league_statistics']['match_count'] = 29
    result = predict(p, CONFIG, asof=ASOF)
    assert result['input_audit']['league_baseline']['reason'] == 'insufficient_league_match_count'


def test_scalar_and_observed_baseline_must_not_conflict():
    p = context_payload(); p['league_avg_goals'] = 2.7
    with pytest.raises(PredictError, match='双重基线'):
        predict(p, CONFIG, asof=ASOF)
    p['league_avg_goals'] = 3
    assert predict(p, CONFIG, asof=ASOF)['input_audit']['league_baseline']['status'] == 'observed_league_statistics'


@pytest.mark.parametrize('field,value', [
    ('available_at', '2030-01-01T10:01:00+08:00'),
    ('feature_as_of', '2030-01-01T10:01:00+08:00'),
    ('available_at', None), ('match_count', True), ('home_goals', -1),
    ('evidence_sha256', 'invalid'),
    ('evidence_sha256', None), ('phase_id', None),
])
def test_invalid_or_future_statistics_rejected(field, value):
    p = context_payload(); p['league_statistics'][field] = value
    with pytest.raises(PredictError, match='league_statistics'):
        predict(p, CONFIG, asof=ASOF)


def test_bare_opponent_numbers_and_injected_marker_do_not_change_forecast():
    p = payload(); base = predict(p, CONFIG, asof=ASOF)
    for row in p['home_recent']:
        row.update(opp_attack=5, opp_defense=5, _opponent_strength_validated=True)
    result = predict(p, CONFIG, asof=ASOF)
    assert result['score_matrix_full'] == base['score_matrix_full']
    assert result['lambda_notes']['opp_adjust_coverage'] == 0
    assert all(a['status'] == 'excluded' for k, a in result['input_audit']['opponent_strength'].items() if k.startswith('home_recent'))


def test_bound_pre_historical_match_opponent_rating_changes_only_supported_rows():
    p = opponent_payload(); before = deepcopy(p)
    no_strength = deepcopy(p); no_strength['home_recent'][0].pop('opponent_strength')
    base = predict(no_strength, CONFIG, asof=ASOF)
    result = predict(p, CONFIG, asof=ASOF)
    assert result['input_audit']['opponent_strength']['home_recent[0]']['status'] == 'accepted'
    assert result['lambda_home_full'] < base['lambda_home_full']
    assert result['lambda_away_full'] < base['lambda_away_full']
    assert result['lambda_notes']['opp_adjust_coverage'] == 0  # away side is still missing
    assert p == before


@pytest.mark.parametrize('field,value', [('team_id', 'wrong'), ('source', 'other'),
                                        ('scale', 'current_rating'), ('sample_scope', 'includes_match')])
def test_unbound_or_wrong_scale_opponent_not_applied(field, value):
    p = opponent_payload(); p['home_recent'][0]['opponent_strength'][field] = value
    result = predict(p, CONFIG, asof=ASOF)
    assert result['input_audit']['opponent_strength']['home_recent[0]']['status'] == 'excluded'


@pytest.mark.parametrize('stamp', ['2029-12-25T20:00:00+08:00', '2029-12-26T00:00:00+08:00'])
def test_current_opponent_rating_cannot_retroactively_adjust_old_match(stamp):
    p = opponent_payload(); p['home_recent'][0]['opponent_strength']['feature_as_of'] = stamp
    with pytest.raises(PredictError, match='早于历史比赛'):
        predict(p, CONFIG, asof=ASOF)


def test_completed_time_alone_cannot_prove_pregame_opponent_window():
    p = opponent_payload(); row = p['home_recent'][0]
    row['completed_at'] = row.pop('played_at')
    result = predict(p, CONFIG, asof=ASOF)
    assert result['input_audit']['opponent_strength']['home_recent[0]']['reason'] == 'historical_match_time_unknown'


def test_default_is_explicit_assumption_and_new_model_revision():
    result = predict(payload(), BASE_CONFIG, asof=ASOF)
    assert result['input_audit']['league_baseline']['status'] == 'model_default_not_observed'
    assert result['model_version'].startswith('v2.11+')
    assert result['pipeline_revision'].endswith('v13')
    assert result['lambda_notes']['baseline_mode'] == 'legacy_v12'


def test_eligible_observed_mean_is_not_silently_promoted_in_default_mode():
    result = predict(context_payload(), BASE_CONFIG, asof=ASOF)
    audit = result['input_audit']['league_baseline']
    assert audit['statistics_status'] == 'eligible_not_enabled'
    assert audit['observed_value'] == 3 and audit['value'] == 2.7


@pytest.mark.parametrize('mode', ['unknown', None, {}])
def test_invalid_strength_mode_rejected(mode):
    with pytest.raises(PredictError, match='strength_baseline_mode'):
        predict(payload(), {**BASE_CONFIG, 'strength_baseline_mode': mode}, asof=ASOF)
