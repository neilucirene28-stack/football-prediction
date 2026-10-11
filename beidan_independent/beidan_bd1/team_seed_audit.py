"""Authenticate additional scoreboard teams before expanding native history scope."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from .archive_io import read_response
from .snapshot import _datetime


def audit_team_seeds(config, root, *, verified_at):
    additional = config.get('additional_team_seed_receipt')
    if additional is None:
        return None
    slug = config['league_slug']
    stage = (config['season_year'], config['season_type'])
    teams = set()
    used = []

    def add(raw, name):
        payload = json.loads(raw)
        leagues = payload.get('leagues', [])
        if len(leagues) != 1 or leagues[0].get('slug') != slug:
            raise ValueError('team seed scoreboard league differs')
        for event in payload.get('events', []):
            season = event.get('season', {})
            if (season.get('year'), season.get('type')) != stage:
                continue
            competitions = event.get('competitions', [])
            if len(competitions) != 1:
                raise ValueError('team seed competition is ambiguous')
            competitors = competitions[0].get('competitors', [])
            if len(competitors) != 2 or {c.get('homeAway') for c in competitors} != {'home', 'away'}:
                raise ValueError('team seed opponents are ambiguous')
            ids = [c.get('team', {}).get('id') for c in competitors]
            if any(not isinstance(t, str) or not t.isdigit() for t in ids) or ids[0] == ids[1]:
                raise ValueError('team seed native identities differ')
            teams.update(ids)
        used.append({'file': name, 'sha256': hashlib.sha256(raw).hexdigest()})

    seeds = config.get('seed_scoreboard_receipts', [])
    if not seeds or len({s['file'] for s in seeds}) != len(seeds):
        raise ValueError('original team seed receipts are missing or duplicated')
    for seed in seeds:
        raw = (root / seed['file']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != seed['sha256']:
            raise ValueError('original team seed differs from receipt')
        add(raw, seed['file'])
    index_path = root / additional['index']
    index_raw = index_path.read_bytes()
    index = json.loads(index_raw)
    receipt = additional['receipt']
    entries = [r for r in index['files'] if r.get('league_slug') == slug]
    if len(entries) != 1 or entries[0] != receipt:
        raise ValueError('additional team seed differs from HTTP index')
    created = _datetime(index['created_at'], 'seed.index.created_at')
    if not (_datetime(receipt['start_utc'], 'seed.start') <=
            _datetime(receipt['end_utc'], 'seed.end') <= created <=
            _datetime(verified_at, 'seed.verified_at')):
        raise ValueError('additional team seed clock order differs')
    name = receipt.get('local_file')
    raw = None
    if name is not None:
        match = re.fullmatch(r'scoreboard_' + re.escape(slug) + r'_(\d{8})\.json', name)
        if match is None:
            raise ValueError('additional team seed filename differs')
        day = match.group(1)
        datetime.strptime(day, '%Y%m%d')
        expected = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={day}'
        if receipt['url'] != expected:
            raise ValueError('additional team seed URL differs')
        raw = read_response(index_path.parent, name)
        if type(receipt['bytes']) is not int or len(raw) != receipt['bytes'] or hashlib.sha256(raw).hexdigest() != receipt['sha256']:
            raise ValueError('additional team seed original differs from HTTP receipt')
    if type(receipt.get('http_status')) is int and receipt['http_status'] == 200 and not receipt.get('error'):
        if raw is None:
            raise ValueError('successful additional team seed lacks original body')
        add(raw, str(index_path.parent / name))
    configured = config['team_ids']
    if len(configured) != len(set(configured)) or set(configured) != teams:
        raise ValueError('configured history teams differ from audited source seeds')
    return {'verified_at': verified_at, 'additional_http_index_sha256': hashlib.sha256(index_raw).hexdigest(),
            'team_ids': sorted(teams, key=int), 'team_n': len(teams), 'used_scoreboards': used,
            'additional_http_status': receipt.get('http_status'), 'canonical_identity_approved': False,
            'capture_clock_independently_authenticated': False}
