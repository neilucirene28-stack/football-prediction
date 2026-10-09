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


def clock():
    return datetime.now(timezone.utc).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('config')
    ap.add_argument('--resume', action='store_true', help='reuse only hash-verified receipt files')
    args = ap.parse_args()
    config = json.loads(Path(args.config).read_bytes())
    root = Path(__file__).resolve().parents[1]
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
        start = clock()
        filename = f'{kind}_{identity}.json'
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                raw = response.read()
                status = response.status
            end = clock()
            payload = json.loads(raw)
            (folder / filename).write_bytes(raw)
            return {'kind': kind, 'local_file': filename, 'url': url,
                    'team_id' if kind == 'schedule' else 'event_id': identity,
                    'start_utc': start, 'end_utc': end, 'http_status': status,
                    'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}, payload
        except Exception as error:
            return {'kind': kind, 'identity': identity, 'url': url,
                    'start_utc': start, 'end_utc': clock(), 'error': str(error)}, None
    files, failed, rows = [], [], []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for receipt, payload in pool.map(fetch, [('schedule', str(t)) for t in config['team_ids']]):
            if payload is None:
                failed.append(receipt)
                continue
            files.append(receipt)
            records, report = parse_schedule(payload, expected_team_id=receipt['team_id'],
                    verified_at=clock(), league_slug=slug, season_year=config['season_year'],
                    season_type=config['season_type'])
            rows.extend(sorted(records, key=lambda r:r['kickoff_at'])[-recent:])
    selected = deduplicate(rows)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for receipt, payload in pool.map(fetch, [('summary', r['provider_match_id']) for r in selected]):
            (files if payload is not None else failed).append(receipt)
    index = {'source_commit': None, 'source': 'direct_ESPN_HTTP_response',
             'created_at': clock(), 'capture_clock_independently_authenticated': False,
             'history_scope': f'up_to_{recent}_recent_FT_events_per_target_team',
             'selected_summary_ids': [r['provider_match_id'] for r in selected],
             'files': files, 'failed_requests': failed}
    (folder/'VERIFIED_FILE_INDEX.json').write_text(json.dumps(index, ensure_ascii=False, indent=2))
    print(json.dumps({'folder':str(folder),'successful_n':len(files), 'failed_n':len(failed),
                      'unique_selected_ft_n':len(selected), 'created_at':index['created_at']}))


if __name__ == '__main__':
    main()
