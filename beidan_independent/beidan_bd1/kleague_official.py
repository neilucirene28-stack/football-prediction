"""Discover K League native fixtures; schedule zeros are never results."""
from datetime import datetime, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo


def extract_fixtures(raw, *, expected_sha256, league_id, year, month):
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('official source digest differs')
    data = json.loads(raw)
    if str(data.get('resultCode')) != '200':
        raise ValueError('official schedule response unsuccessful')
    rows, identities = [], set()
    for row in data['data']['scheduleList']:
        if row['leagueId'] != league_id or row['year'] != year:
            raise ValueError('schedule competition/season differs')
        date = datetime.strptime(row['gameDate'], '%Y.%m.%d')
        if date.month != month or date.year != year:
            raise ValueError('schedule month differs')
        if not re.fullmatch(r'\d{2}:\d{2}', row.get('gameTime', '')):
            raise ValueError('explicit kickoff time required')
        kickoff = datetime.strptime(row['gameDate'] + ' ' + row['gameTime'], '%Y.%m.%d %H:%M').replace(tzinfo=ZoneInfo('Asia/Seoul'))
        identity = f"{year}:{league_id}:{row['gameId']}"
        if identity in identities:
            raise ValueError('duplicate native fixture')
        identities.add(identity)
        home, away = row['homeTeam'], row['awayTeam']
        if not all(isinstance(t, str) and re.fullmatch(r'K\d+', t) for t in (home, away)) or home == away:
            raise ValueError('distinct native team IDs required')
        rows.append({'provider': 'kleague_official', 'provider_match_id': identity,
                     'competition': league_id, 'season': year, 'round': row['roundId'],
                     'home_team_id': home, 'away_team_id': away,
                     'home_name': row['homeTeamName'], 'away_name': row['awayTeamName'],
                     'kickoff_at': kickoff.astimezone(timezone.utc).isoformat(),
                     'source_state': row.get('gameStatus'), 'source_end_yn': row.get('endYn'),
                     'source_sha256': expected_sha256, 'beidan_binding_approved': False,
                     'eligible_for_training': False})
    return rows
