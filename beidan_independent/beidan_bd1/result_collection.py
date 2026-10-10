"""Collect due results against sealed probabilities, retaining HTTP originals."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

from .frozen_results import import_espn_result
from .frozen_settlement import load_bundle, settle_bundle
from .http_archive import capture_response
from .artifact_io import read_artifact
from .snapshot import _datetime


def clock():
    return datetime.now(timezone.utc).isoformat()


def due_for_collection(fixture, now):
    # This is a polling delay, not evidence that a fixture has finished.
    return _datetime(now, 'now') >= _datetime(fixture['kickoff_at'], 'kickoff') + timedelta(minutes=105)


def frozen_league_slugs(report):
    """Resolve slugs from the exact retained pre-match history audits."""
    # Resolve slugs only from the exact audits retained by the freeze.
    root = Path(__file__).resolve().parents[1]
    slugs = {}
    for name, expected in report['prospective_freeze']['history_audits_sha256'].items():
        path = Path(name)
        marker = '/beidan_independent/'
        if path.is_absolute() and marker in name:
            path = root / name.split(marker, 1)[1]
        elif not path.is_absolute():
            path = root / path
        raw = read_artifact(path)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('original history audit digest differs')
        for row in json.loads(raw)['research_predictions']:
            record = row['binding']
            key = record['provider_league_id']
            slug = record['provider_league_slug']
            if key in slugs and slugs[key] != slug:
                raise ValueError('ambiguous provider league slug')
            slugs[key] = slug
    return slugs


def collect(bundle, manifest_sha256, folder, *, opener=urllib.request.urlopen, only_match_ids=None):
    now = clock()
    report, pool, predictions = load_bundle(bundle, manifest_sha256=manifest_sha256, evaluated_at=now)
    if report['identity_mode'] != 'provider_native_espn':
        raise ValueError('collector requires an ESPN native research freeze')
    scope = None
    if only_match_ids is not None:
        if isinstance(only_match_ids, (str, bytes)):
            raise ValueError('collection scope must be a sequence of match IDs')
        ids = list(only_match_ids)
        if not ids or any(not isinstance(mid, str) or not mid for mid in ids) or len(set(ids)) != len(ids):
            raise ValueError('collection scope must contain unique nonempty predicted match IDs')
        scope = set(ids)
        if not scope <= set(predictions):
            raise ValueError('collection scope includes an unknown or originally blocked match')
    root = Path(__file__).resolve().parents[1]
    execution_code_sha256 = {name: hashlib.sha256((root / 'beidan_bd1' / name).read_bytes()).hexdigest()
                            for name in ('result_collection.py', 'http_archive.py',
                                         'frozen_results.py', 'espn_summary.py')}
    slugs = frozen_league_slugs(report)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    receipts, ledger, results = [], [], []
    for identity, fixture in pool.items():
        if identity not in predictions:
            ledger.append({'match_id': identity, 'status': 'originally_blocked'})
            continue
        if scope is not None and identity not in scope:
            ledger.append({'match_id': identity, 'status': 'outside_collection_scope'})
            continue
        if not due_for_collection(fixture, now):
            ledger.append({'match_id': identity, 'status': 'not_due'})
            continue
        slug = slugs.get(fixture['provider_league_id'])
        if slug is None:
            raise ValueError('frozen provider league has no audited slug')
        eid = fixture['provider_match_id']
        url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary?event={eid}'
        receipt, raw = capture_response(url, folder, f'summary_{eid}.json', opener=opener)
        receipt['match_id'] = identity
        try:
            if raw is None:
                raise ValueError(receipt['error'])
            if receipt['http_status'] != 200:
                raise ValueError('unsuccessful HTTP status')
            # The fresh verification clock is never copied from kickoff or publication.
            record = import_espn_result(raw, pool={identity: fixture}, verified_at=clock(),
                                        expected_league_slug=slug)
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
    index['execution_code_sha256'] = execution_code_sha256
    index['collection_scope_match_ids'] = sorted(scope) if scope is not None else None
    for name, value in [('COLLECTION_INDEX.json', index), ('settlement.json', settlement)]:
        with (folder / name).open('x') as fh:
            json.dump(value, fh, ensure_ascii=False, allow_nan=False, indent=2)
    return settlement


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--manifest-sha256', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--match-id', action='append', help='collect only these frozen predictions; repeat for multiple IDs')
    args = ap.parse_args()
    result = collect(args.bundle, args.manifest_sha256, args.out, only_match_ids=args.match_id)
    print(json.dumps({k: result[k] for k in ('offered_n', 'predicted_n', 'blocked_n', 'paired_n', 'brier_candidate', 'refitted', 'reselected_after_results')}))


if __name__ == '__main__':
    main()
