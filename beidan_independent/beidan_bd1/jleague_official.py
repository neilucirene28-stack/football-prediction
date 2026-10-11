"""Extract explicit native fixtures from retained J.LEAGUE Flight payloads.

This is source discovery, not approval of a Chinese Beidan team binding. No
placeholder scores, narrative results or standings are training observations.
"""
from datetime import datetime, timezone
import hashlib
import json
import re


def flight_objects(raw):
    text = raw.decode('utf-8', errors='strict')
    decoder = json.JSONDecoder()
    chunks = []
    for match in re.finditer(r'self\.__next_f\.push\(', text):
        try:
            value, _ = decoder.raw_decode(text[match.end():])
        except ValueError:
            continue
        if (isinstance(value, list) and len(value) == 2 and
                value[0] == 1 and isinstance(value[1], str)):
            chunks.append(value[1])
    for line in ''.join(chunks).splitlines():
        match = re.fullmatch(r'[0-9a-f]+:(.*)', line)
        if match:
            try:
                yield json.loads(match[1])
            except ValueError:
                continue


def dictionaries(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from dictionaries(child)
    elif isinstance(value, list):
        for child in value:
            yield from dictionaries(child)


def extract_fixture(raw, *, source_url, expected_sha256):
    """Require one dated native fixture on an observed official detail URL."""
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('official source digest differs')
    match = re.fullmatch(r'https://www\.jleague\.jp/match/(j[123])/(\d{4})/(\d{6})/?', source_url)
    if not match:
        raise ValueError('not an official league match detail URL')
    found = []
    for obj in flight_objects(raw):
        for row in dictionaries(obj):
            if row.get('variant') != 'game-details':
                continue
            if row.get('tournament') != match[1]:
                raise ValueError('detail competition differs')
            home, away = row.get('homeTeam', {}), row.get('awayTeam', {})
            stamp = row.get('date', '')
            if not stamp.startswith('$D') or row.get('isKickoffTimeUndecided') is not False:
                raise ValueError('explicit kickoff is required')
            kickoff = datetime.fromisoformat(stamp[2:].replace('Z', '+00:00'))
            if kickoff.tzinfo is None:
                raise ValueError('kickoff timezone missing')
            purchase = row.get('purchaseWinner', {})
            if purchase.get('gameDate') != match[2] + match[3][:4]:
                raise ValueError('official event date differs from URL')
            if (not str(home.get('teamId', '')).isdigit() or
                    not str(away.get('teamId', '')).isdigit() or
                    home['teamId'] == away['teamId']):
                raise ValueError('distinct native team IDs required')
            found.append({
                'provider': 'jleague_official',
                'provider_match_id': match[2] + match[3],
                'competition': match[1], 'section': row.get('section'),
                'kickoff_at': kickoff.astimezone(timezone.utc).isoformat(),
                'home_team_id': str(home['teamId']), 'away_team_id': str(away['teamId']),
                'home_name': home.get('name'), 'away_name': away.get('name'),
                'source_state': row.get('type'),
                'source_url': source_url, 'source_sha256': expected_sha256,
                'beidan_binding_approved': False,
                'eligible_for_training': False,
            })
    unique = {json.dumps(row, sort_keys=True): row for row in found}
    if len(unique) != 1:
        raise ValueError('exactly one explicit native fixture required')
    return next(iter(unique.values()))
