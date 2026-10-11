"""Verify native historical HTTP evidence before exposing it to new forecasts."""
from datetime import datetime, timezone
import hashlib
import json
from .archive_io import read_response
from .espn_schedule import parse_schedule, deduplicate
from .espn_summary import audit_summary
from .snapshot import _datetime


def clock():
    return datetime.now(timezone.utc).isoformat()


def audit_history(config, root, *, now=clock):
    folder = root / config['folder']
    index_raw = (folder / 'VERIFIED_FILE_INDEX.json').read_bytes()
    index = json.loads(index_raw)
    started = now()
    started_dt = _datetime(started, 'audit.started_at')
    index_created = _datetime(index['created_at'], 'index.created_at')
    if index_created > started_dt:
        raise ValueError('history index is not available at audit start')
    slug = config['league_slug']
    rows, summaries, checks, observed = [], {}, [], set()
    for entry in index['files'] + index['failed_requests']:
        kind = entry['kind']
        if kind not in ('schedule', 'summary'):
            raise ValueError('unknown history receipt kind')
        identity = entry['team_id' if kind == 'schedule' else 'event_id']
        key = (kind, identity)
        if key in observed:
            raise ValueError('duplicate history response identity')
        observed.add(key)
        suffix = f'teams/{identity}/schedule' if kind == 'schedule' else f'summary?event={identity}'
        if entry['url'] != f'https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/{suffix}':
            raise ValueError('history receipt URL differs from requested native identity')
        if not (_datetime(entry['start_utc'], 'start') <= _datetime(entry['end_utc'], 'end') <= index_created <= started_dt):
            raise ValueError('history receipt clock order differs')
        raw = None
        if 'local_file' in entry:
            raw = read_response(folder, entry['local_file'])
            if (type(entry['bytes']) is not int or len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']):
                raise ValueError('history original differs from HTTP receipt')
        successful = not entry.get('error') and type(entry.get('http_status')) is int and entry['http_status'] == 200
        if not successful:
            if not entry.get('error'):
                raise ValueError('failed history receipt is missing its reason')
            continue
        if raw is None:
            raise ValueError('successful history receipt lacks original body')
        payload = json.loads(raw)
        if kind == 'schedule':
            try:
                records, report = parse_schedule(payload, expected_team_id=identity, verified_at=started,
                     league_slug=slug, season_year=config['season_year'], season_type=config['season_type'])
            except ValueError as error:
                checks.append({'file': entry['local_file'], 'rejected': True, 'reason': str(error)})
                continue
            rows.extend(records)
            checks.append({'file': entry['local_file'], **report})
        else:
            if payload.get('header', {}).get('id') != identity:
                raise ValueError('summary body differs from requested event ID')
            summaries[identity] = raw
    schedule_ids = {identity for kind, identity in observed if kind == 'schedule'}
    if schedule_ids != set(config['team_ids']):
        raise ValueError('history receipt schedule scope differs from configuration')
    selected = index['selected_summary_ids']
    if len(selected) != len(set(selected)) or {identity for kind, identity in observed if kind == 'summary'} != set(selected):
        raise ValueError('history receipt summary scope differs from selection')
    unique = deduplicate(rows)
    if not set(selected) <= {r['provider_match_id'] for r in unique}:
        raise ValueError('selected history summary is outside audited schedule union')
    audited, missing, rejected = [], [], []
    for record in unique:
        raw = summaries.get(record['provider_match_id'])
        if raw is None:
            missing.append(record['provider_match_id'])
            continue
        try:
            row, audit = audit_summary(json.loads(raw), record, verified_at=started, raw_bytes=raw, league_slug=slug)
            audited.append({'record': row, 'audit': audit})
        except ValueError as error:
            rejected.append({'event_id': record['provider_match_id'], 'reason': str(error)})
    completed = now()
    if _datetime(completed, 'audit.completed_at') < started_dt:
        raise ValueError('history audit completion clock precedes start')
    for item in audited:
        item['record']['verified_at'] = completed
    return {'verified_at': completed, 'verification_started_at': started,
            'file_index_sha256': hashlib.sha256(index_raw).hexdigest(),
            'source_commit': index.get('source_commit'), 'schedule_observations_n': len(rows),
            'unique_ft_n': len(unique), 'summary_raw_n': len(summaries), 'audited_n': len(audited),
            'explicit_ht_n': sum(r['audit']['explicit_halftime_available'] for r in audited),
            'missing_summary_ids': missing, 'rejected': rejected, 'schedule_checks': checks,
            'failed_http_requests_n': len(index['failed_requests']), 'receipts_n': len(observed), 'results': audited,
            'source_scope': 'observed schedule union; not independently proven full league season',
            'capture_clock_independently_authenticated': False}
