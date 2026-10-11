"""Compare literal source observations without joining provider ID spaces."""
import unicodedata
from .snapshot import _datetime


def _literal(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('source team name missing')
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def compare_native_fixture(binding, af_records):
    names = tuple(_literal(binding[k]) for k in ('provider_home', 'provider_away'))
    kickoff = _datetime(binding['kickoff_at'], 'native kickoff')
    matches = []
    for row in af_records:
        if (_datetime(row['kickoff_at'], 'AF kickoff') == kickoff
                and tuple(_literal(row[k]) for k in ('home', 'away')) == names):
            matches.append({'af_event_id': row['provider_match_id'],
                            'af_home_id': row['provider_home_id'],
                            'af_away_id': row['provider_away_id'],
                            'af_league': row['league'], 'af_status': row['status'],
                            'season_year_label_equal': row['league']['season'] == binding['season_year']})
    return {'espn_event_id': binding['proposed_provider_event_id'],
            'espn_league_id': binding['provider_league_id'],
            'espn_season_year': binding['season_year'], 'espn_season_type': binding['season_type'],
            'kickoff_at': binding['kickoff_at'], 'matches': matches,
            'literal_name_and_utc_match_n': len(matches),
            'unique_literal_name_and_utc_match': len(matches) == 1,
            'season_stage_equivalence_approved': False,
            'cross_provider_id_join_approved': False,
            'canonical_identity_approved': False, 'model_imported': False,
            'production_eligible': False}
