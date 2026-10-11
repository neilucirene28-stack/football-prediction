"""Auditable disciplinary observations; no unvalidated goal multipliers."""
from datetime import datetime, timezone
import hashlib
import json

from .espn_summary import audit_summary
from .snapshot import _datetime


def _count(value):
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise ValueError('card/foul counts must be explicit nonnegative integers')


def extract_observation(raw, *, schedule, league_slug, source_available_at,
                        verified_at, expected_sha256):
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('discipline original digest differs')
    available = _datetime(source_available_at, 'source_available_at')
    verified = _datetime(verified_at, 'verified_at')
    if available > verified:
        raise ValueError('discipline source is not yet available')
    payload = json.loads(raw)
    audit_summary(payload, schedule, raw_bytes=raw, verified_at=verified_at, league_slug=league_slug)
    if available <= _datetime(schedule['kickoff_at'], 'kickoff_at'):
        raise ValueError('completed card statistics cannot be available before kickoff')
    teams = payload.get('boxscore', {}).get('teams', [])
    if len(teams) != 2 or {r.get('homeAway') for r in teams} != {'home', 'away'}:
        raise ValueError('discipline team scope ambiguous')
    sides = {}
    for row in teams:
        side = row['homeAway']
        if row.get('team', {}).get('id') != schedule[f'provider_{side}_id']:
            raise ValueError('discipline team ID differs from audited result')
        stats = row.get('statistics', [])
        names = [r.get('name') for r in stats]
        if len(names) != len(set(names)):
            raise ValueError('duplicate statistic name')
        lookup = {r['name']: r for r in stats}
        sides[side] = {'team_id': schedule[f'provider_{side}_id']}
        for output, key in [('yellow_cards', 'yellowCards'), ('red_cards', 'redCards'), ('fouls', 'foulsCommitted')]:
            sides[side][output] = _count(lookup[key].get('displayValue')) if key in lookup else None
    return {'provider': 'espn', 'provider_match_id': schedule['provider_match_id'],
            'provider_league_id': schedule['provider_league_id'],
            'season_year': schedule['season_year'], 'season_type': schedule['season_type'],
            'kickoff_at': schedule['kickoff_at'], 'source_available_at': source_available_at,
            'verified_at': verified_at, 'source_sha256': expected_sha256, 'sides': sides,
            'card_scope': 'provider_team_totals_on_field_scope_unverified',
            'direct_vs_second_yellow_red_split': None, 'on_field_dismissal_probability': None,
            'production_eligible': False}


def team_features(observations, *, team_id, league_id, season_year, season_type,
                  asof_at, exclude_match_id=None):
    """Describe retained pre-asof observations, never claim season completeness."""
    asof = _datetime(asof_at, 'asof_at')
    retained = {}
    for row in observations:
        if row['provider'] != 'espn':
            raise ValueError('provider scope differs')
        available = max(_datetime(row['source_available_at'], 'available'),
                        _datetime(row['verified_at'], 'verified'))
        if available > asof:
            continue
        eid = row['provider_match_id']
        fields = ('provider_league_id', 'season_year', 'season_type', 'kickoff_at', 'sides', 'card_scope')
        if eid in retained:
            if any(row[k] != retained[eid][k] for k in fields):
                raise ValueError('conflicting duplicate disciplinary event')
            continue
        retained[eid] = row
    rows = []
    for eid, row in retained.items():
        if eid == exclude_match_id or (row['provider_league_id'], row['season_year'], row['season_type']) != (league_id, season_year, season_type):
            continue
        if _datetime(row['kickoff_at'], 'kickoff_at') >= asof:
            raise ValueError('completed disciplinary event kickoff is not in the past')
        for side, stats in row['sides'].items():
            if stats['team_id'] == team_id:
                for key in ('yellow_cards', 'red_cards', 'fouls'):
                    if stats[key] is not None:
                        _count(stats[key])
                rows.append((row, side, stats))
    rows.sort(key=lambda r: _datetime(r[0]['kickoff_at'], 'kickoff_at'))

    def describe(items):
        output = {'observed_matches_n': len(items)}
        for key in ('yellow_cards', 'red_cards', 'fouls'):
            values = [r[2][key] for r in items if r[2][key] is not None]
            n = len(values)
            output[key] = {'known_matches_n': n, 'missing_matches_n': len(items)-n,
                           'observed_total': sum(values) if n else None,
                           'per_known_match': sum(values)/n if n else None}
            if key != 'fouls':
                output[key]['empirical_at_least_one_fraction'] = sum(v > 0 for v in values)/n if n else None
        return output

    return {'team_id': team_id, 'league_id': league_id, 'season_year': season_year,
            'season_type': season_type, 'asof_at': asof_at,
            'coverage': 'partial_retained_current_season_stage_history',
            'full_season_total': None, 'suspension_status': None,
            'on_field_dismissal_probability': None,
            'probability_status': 'empirical_observation_only_not_calibrated_forecast',
            'all_observed': describe(rows), 'latest_5_observed': describe(rows[-5:]),
            'latest_10_observed': describe(rows[-10:]),
            'home_observed': describe([r for r in rows if r[1] == 'home']),
            'away_observed': describe([r for r in rows if r[1] == 'away']),
            'used_provider_match_ids': [r[0]['provider_match_id'] for r in rows],
            'used_observations': [{k:r[0][k] for k in ('provider_match_id', 'source_sha256',
                                                       'source_available_at', 'verified_at')} for r in rows],
            'production_eligible': False, 'goal_model_adjusted': False}
