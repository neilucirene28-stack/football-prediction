"""Fetch original ESPN soccer responses with actual receipt clocks.

Observe a configured bounded number of recent regular-time games per team.
This is a bounded schedule union, not a claim of complete league history.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request
from beidan_bd1.espn_schedule import parse_schedule, deduplicate
from beidan_bd1.archive_io import read_response
from beidan_bd1.http_archive import capture_response


def clock():
    return datetime.now(timezone.utc).isoformat()


def main(*, opener=urllib.request.urlopen):
    ap = argparse.ArgumentParser()
    ap.add_argument('config')
    ap.add_argument('--resume', action='store_true', help='reuse only hash-verified receipt files')
    args = ap.parse_args()
    config = json.loads(Path(args.config).read_bytes())
    root = Path(__file__).resolve().parents[1]
    code_sha256 = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   for name in ('scripts/mine_espn_native.py', 'beidan_bd1/http_archive.py',
                                'beidan_bd1/espn_schedule.py', 'beidan_bd1/archive_io.py')}
    folder = root / config['folder']
    cache = {}
    if args.resume:
        index = json.loads((folder / 'VERIFIED_FILE_INDEX.json').read_bytes())
        for receipt in index['files']:
            raw = read_response(folder, receipt['local_file'])
            if len(raw) != receipt['bytes'] or hashlib.sha256(raw).hexdigest() != receipt['sha256']:
                raise ValueError('cached raw response differs from original receipt')
            cache[receipt['kind'], receipt.get('team_id', receipt.get('event_id'))] = receipt
    else:
        folder.mkdir(parents=True, exist_ok=False)
    slug = config['league_slug']
    recent = config.get('recent_per_team', 6)
    if isinstance(recent, bool) or not isinstance(recent, int) or not 1 <= recent <= 50:
        raise ValueError('recent_per_team must be an integer from 1 to 50')
    def fetch(job):
        kind, identity = job
        if job in cache:
            receipt = cache[job]
            return receipt, json.loads(read_response(folder, receipt['local_file']))
        suffix = f'teams/{identity}/schedule' if kind == 'schedule' else f'summary?event={identity}'
        url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/{suffix}'
        filename = f'{kind}_{identity}.json'
        if (folder / filename).exists():
            # Resuming a previously failed payload must retain that attempt.
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            filename = f'{kind}_{identity}_{stamp}.json'
        receipt, raw = capture_response(url, folder, filename, opener=opener)
        receipt.update(kind=kind)
        receipt['team_id' if kind == 'schedule' else 'event_id'] = identity
        try:
            if raw is None:
                raise ValueError(receipt['error'])
            if receipt['http_status'] != 200:
                raise ValueError('unsuccessful HTTP status')
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('provider payload must be a JSON object')
            return receipt, payload
        except Exception as error:
            receipt['error'] = str(error)
            return receipt, None
    files, failed, rows, rejected_schedules = [], [], [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for receipt, payload in pool.map(fetch, [('schedule', str(t)) for t in config['team_ids']]):
            if payload is None:
                failed.append(receipt)
                continue
            files.append(receipt)
            try:
                records, report = parse_schedule(payload, expected_team_id=receipt['team_id'],
                        verified_at=clock(), league_slug=slug, season_year=config['season_year'],
                        season_type=config['season_type'])
            except ValueError as error:
                rejected_schedules.append({'local_file': receipt['local_file'], 'reason': str(error)})
                continue
            rows.extend(sorted(records, key=lambda r:r['kickoff_at'])[-recent:])
    selected = deduplicate(rows)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for receipt, payload in pool.map(fetch, [('summary', r['provider_match_id']) for r in selected]):
            (files if payload is not None else failed).append(receipt)
    index = {'source_commit': None, 'source': 'direct_ESPN_HTTP_response',
             'created_at': clock(), 'capture_clock_independently_authenticated': False,
             'history_scope': f'up_to_{recent}_recent_FT_events_per_target_team',
             'selected_summary_ids': [r['provider_match_id'] for r in selected],
             'files': files, 'failed_requests': failed, 'rejected_schedules': rejected_schedules,
             'execution_code_sha256': code_sha256}
    (folder/'VERIFIED_FILE_INDEX.json').write_text(json.dumps(index, ensure_ascii=False, indent=2))
    print(json.dumps({'folder':str(folder),'successful_n':len(files), 'failed_n':len(failed),
                      'unique_selected_ft_n':len(selected), 'created_at':index['created_at']}))


if __name__ == '__main__':
    main()
