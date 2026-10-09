"""Collect due results against sealed probabilities, retaining HTTP originals."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

from .frozen_results import import_espn_result
from .frozen_settlement import load_bundle, settle_bundle
from .snapshot import _datetime


def clock():
    return datetime.now(timezone.utc).isoformat()


def due_for_collection(fixture, now):
    # This is a polling delay, not evidence that a fixture has finished.
    return _datetime(now, 'now') >= _datetime(fixture['kickoff_at'], 'kickoff') + timedelta(minutes=105)


def collect(bundle, manifest_sha256, folder, *, opener=urllib.request.urlopen):
    now = clock()
    report, pool, predictions = load_bundle(bundle, manifest_sha256=manifest_sha256, evaluated_at=now)
    if report['identity_mode'] != 'provider_native_espn':
        raise ValueError('collector requires an ESPN native research freeze')
    # Resolve slugs only from the exact audits retained by the freeze.
    root = Path(__file__).resolve().parents[1]
    slugs = {}
    for name, expected in report['prospective_freeze']['history_audits_sha256'].items():
        path = Path(name)
        if not path.is_absolute():
            path = root / path
        if not path.exists():
            marker = '/beidan_independent/'
            if marker in name:
                path = root / name.split(marker, 1)[1]
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('original history audit digest differs')
        for row in json.loads(raw)['research_predictions']:
            record = row['binding']
            key = record['provider_league_id']
            slug = record['provider_league_slug']
            if key in slugs and slugs[key] != slug:
                raise ValueError('ambiguous provider league slug')
            slugs[key] = slug
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    receipts, ledger, results = [], [], []
    for identity, fixture in pool.items():
        if identity not in predictions:
            ledger.append({'match_id': identity, 'status': 'originally_blocked'})
            continue
        if not due_for_collection(fixture, now):
            ledger.append({'match_id': identity, 'status': 'not_due'})
            continue
        slug = slugs.get(fixture['provider_league_id'])
        if slug is None:
            raise ValueError('frozen provider league has no audited slug')
        eid = fixture['provider_match_id']
        url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={eid}'
        receipt = {'match_id': identity, 'url': url, 'start_utc': clock()}
        try:
            with opener(url, timeout=45) as response:
                raw, status = response.read(), response.status
            receipt.update(end_utc=clock(), http_status=status, bytes=len(raw),
                           sha256=hashlib.sha256(raw).hexdigest(), local_file=f'summary_{eid}.json')
            (folder / receipt['local_file']).write_bytes(raw)
            if status != 200:
                raise ValueError('unsuccessful HTTP status')
            # The fresh verification clock is never copied from kickoff or publication.
            record = import_espn_result(raw, pool=pool, verified_at=clock())
            results.append(record)
            ledger.append({'match_id': identity, 'status': 'verified_regular_time_ft'})
        except Exception as error:
            receipt.setdefault('end_utc', clock())
            receipt['error'] = str(error)
            ledger.append({'match_id': identity, 'status': 'pending_or_rejected', 'reason': str(error)})
        receipts.append(receipt)
    with (folder / 'results.jsonl').open('x') as fh:
        for result in results:
            fh.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n')
    settlement = settle_bundle(bundle, manifest_sha256=manifest_sha256, results=results, evaluated_at=clock())
    index = {'bundle': str(bundle), 'manifest_sha256': manifest_sha256, 'started_at': now,
             'completed_at': clock(), 'receipts': receipts, 'complete_pool_ledger': ledger,
             'original_probabilities_only': True, 'refitted': False, 'reselected': False}
    for name, value in [('COLLECTION_INDEX.json', index), ('settlement.json', settlement)]:
        with (folder / name).open('x') as fh:
            json.dump(value, fh, ensure_ascii=False, allow_nan=False, indent=2)
    return settlement


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--manifest-sha256', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    result = collect(args.bundle, args.manifest_sha256, args.out)
    print(json.dumps({k: result[k] for k in ('offered_n', 'predicted_n', 'blocked_n', 'paired_n', 'brier_candidate', 'refitted', 'reselected_after_results')}))


if __name__ == '__main__':
    main()
