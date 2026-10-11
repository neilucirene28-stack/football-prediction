"""Audit retained HTTP collections, merge unique results and plan the next poll.

Retained digests detect changes; they do not independently authenticate clocks
or official Beidan identities. This module never fits or selects a model.
"""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from .artifact_io import read_artifact
from .frozen_results import import_espn_result
from .frozen_settlement import load_bundle, NATIVE_IDS
from .result_collection import due_for_collection, frozen_league_slugs
from .snapshot import _datetime
from .unique_settlement import resolve_bundle, settle_unique


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def indexed(rows, label):
    value = {r['match_id']: r for r in rows}
    if len(value) != len(rows):
        raise ValueError(f'duplicate {label} match ID')
    return value


def audit_collection(folder, expected_index_sha256, *, bundles, evaluated_at):
    folder = Path(folder)
    raw_index = (folder / 'COLLECTION_INDEX.json').read_bytes()
    if digest(raw_index) != expected_index_sha256:
        raise ValueError('collection index digest differs')
    index = json.loads(raw_index)
    key = (index['bundle'], index['manifest_sha256'])
    if key not in bundles:
        raise ValueError('collection freeze is not in the authenticated registry')
    report, pool, predictions = bundles[key]
    if (report['identity_mode'] != 'provider_native_espn' or
            index.get('original_probabilities_only') is not True or
            index.get('refitted') is not False or index.get('reselected') is not False):
        raise ValueError('collection must retain native original probabilities')
    started = _datetime(index['started_at'], 'collection.started_at')
    completed = _datetime(index['completed_at'], 'collection.completed_at')
    if not started <= completed <= _datetime(evaluated_at, 'evaluated_at'):
        raise ValueError('collection clock order is invalid')
    scope = index.get('collection_scope_match_ids')
    if scope is not None and (not isinstance(scope, list) or not scope or
                             any(not isinstance(mid, str) or not mid for mid in scope) or
                             len(set(scope)) != len(scope) or not set(scope) <= set(predictions)):
        raise ValueError('invalid collection scope')
    scope = set(scope) if scope is not None else set(predictions)
    ledger = indexed(index['complete_pool_ledger'], 'ledger')
    receipts = indexed(index['receipts'], 'receipt')
    if set(ledger) != set(pool):
        raise ValueError('collection lost complete pool rows')
    requested = set()
    for mid, fixture in pool.items():
        if mid not in predictions:
            status = 'originally_blocked'
        elif mid not in scope:
            status = 'outside_collection_scope'
        elif not due_for_collection(fixture, index['started_at']):
            status = 'not_due'
        else:
            requested.add(mid)
            if ledger[mid]['status'] not in ('verified_regular_time_ft', 'pending_or_rejected'):
                raise ValueError('requested event has invalid ledger status')
            continue
        if ledger[mid]['status'] != status:
            raise ValueError('collection changed an original pool or polling status')
    if set(receipts) != requested:
        raise ValueError('HTTP receipts differ from requested event set')
    raw_results = (folder / 'results.jsonl').read_bytes()
    lines = [line for line in raw_results.splitlines(keepends=True) if line.strip()]
    records = [json.loads(line) for line in lines]
    results = indexed(records, 'result')
    verified_ids = {mid for mid, row in ledger.items() if row['status'] == 'verified_regular_time_ft'}
    if set(results) != verified_ids:
        raise ValueError('results differ from verified ledger IDs')
    slugs = frozen_league_slugs(report)
    for mid, receipt in receipts.items():
        fixture = pool[mid]
        slug = slugs[fixture['provider_league_id']]
        expected_url = (f'https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/summary'
                        f'?event={fixture["provider_match_id"]}')
        if receipt['url'] != expected_url:
            raise ValueError('receipt URL differs from frozen event and league')
        start = _datetime(receipt['start_utc'], 'receipt.start_utc')
        end = _datetime(receipt['end_utc'], 'receipt.end_utc')
        if not started <= start <= end <= completed:
            raise ValueError('receipt clock order is invalid')
        raw = None
        if 'local_file' in receipt:
            name = receipt['local_file']
            if not isinstance(name, str) or Path(name).name != name or name in ('.', '..'):
                raise ValueError('unsafe receipt filename')
            path = folder / name
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError('receipt escapes collection folder')
            raw = read_artifact(path)
            if type(receipt['bytes']) is not int or len(raw) != receipt['bytes'] or digest(raw) != receipt['sha256']:
                raise ValueError('HTTP original differs from receipt')
        elif not receipt.get('error') or any(k in receipt for k in ('bytes', 'sha256')):
            raise ValueError('missing HTTP original without a transport error')
        if mid not in results:
            if not receipt.get('error'):
                raise ValueError('rejected receipt is missing its reason')
            continue
        record = results[mid]
        verified = _datetime(record['verified_at'], 'result.verified_at')
        if not end <= verified <= completed:
            raise ValueError('result verification predates receipt or follows collection')
        if raw is None or type(receipt.get('http_status')) is not int or receipt['http_status'] != 200 or receipt.get('error'):
            raise ValueError('verified result lacks a successful HTTP original')
        replayed = import_espn_result(raw, pool={mid: fixture}, verified_at=record['verified_at'],
                                      expected_league_slug=slug)
        if replayed != record:
            raise ValueError('stored result differs from reimported HTTP original')
    evidence = {'collection': str(folder), 'manifest_sha256': key[1],
                'index_sha256': expected_index_sha256, 'results_sha256': digest(raw_results),
                'results_n': len(records), 'receipts_n': len(receipts), 'pool_ledger_n': len(ledger)}
    return [(record, line, str(folder)) for record, line in zip(records, lines)], evidence


def plan_pending(registry, settlement, bundles, *, evaluated_at):
    owned = {(row['period'], row['match_id']): row for row in registry['rows']}
    now = _datetime(evaluated_at, 'evaluated_at')
    rows, groups = [], {}
    for row in settlement['matches']:
        mid = row['match_id']
        identity = (row['period'], mid)
        if identity not in owned:
            rows.append({'period': row['period'], 'match_id': mid, 'status': 'originally_uncovered'})
            continue
        owner = owned[identity]
        key = (owner['bundle'], owner['manifest_sha256'])
        fixture = bundles[key][1][mid]
        due = _datetime(fixture['kickoff_at'], 'kickoff_at') + timedelta(minutes=105)
        status = ('settled' if row['status'] == 'settled_frozen_probabilities'
                  else 'due_for_poll' if now >= due else 'not_due')
        rows.append({'period': row['period'], 'match_id': mid, 'status': status,
                     'kickoff_at': fixture['kickoff_at'], 'poll_not_before': due.isoformat(),
                     'bundle': key[0], 'manifest_sha256': key[1],
                     'provider_match_id': fixture['provider_match_id']})
        if status == 'due_for_poll':
            groups.setdefault(key, []).append(mid)
    next_poll = min((r['poll_not_before'] for r in rows if r['status'] == 'not_due'), default=None)
    return {'created_at': evaluated_at, 'offered_n': len(rows), 'predicted_n': len(owned),
            'settled_n': settlement['paired_n'],
            'pending_n': sum(r['status'] in ('due_for_poll', 'not_due') for r in rows),
            'due_for_poll_n': sum(r['status'] == 'due_for_poll' for r in rows),
            'next_poll_not_before': next_poll, 'rows': rows,
            'collection_groups': [{'bundle': k[0], 'manifest_sha256': k[1], 'match_ids': mids}
                                  for k, mids in groups.items()],
            'poll_delay_is_not_full_time_evidence': True, 'production_gate_passed': False}


def merge_collections(registry_path, registry_sha256, collections, out, *, evaluated_at=None, root=None):
    now = evaluated_at or datetime.now(timezone.utc).isoformat()
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    # Authenticate earliest ownership before trusting any registry rows or sources.
    settle_unique(registry_path, registry_sha256=registry_sha256, results=[], evaluated_at=now, root=root)
    registry = json.loads(Path(registry_path).read_bytes())
    bundles = {(e['path'], e['manifest_sha256']): load_bundle(resolve_bundle(e['path'], root),
               manifest_sha256=e['manifest_sha256'], evaluated_at=now) for e in registry['bundles']}
    specs = list(collections)
    if not specs or len({str(Path(folder).resolve()) for folder, _ in specs}) != len(specs):
        raise ValueError('collections must be nonempty unique directories')
    observations, sources = [], []
    for folder, expected in specs:
        rows, evidence = audit_collection(folder, expected, bundles=bundles, evaluated_at=now)
        observations.extend(rows)
        sources.append(evidence)
    chosen, duplicates = {}, []
    for record, line, source in sorted(observations, key=lambda x: (_datetime(x[0]['verified_at'], 'verified_at'), x[2])):
        identity = (record['period'], record['match_id'])
        if identity in chosen:
            original = chosen[identity][0]
            fields = (*NATIVE_IDS, 'ft_home', 'ft_away', 'ht_home', 'ht_away', 'regular_time')
            if (any(record.get(k) != original.get(k) for k in fields) or
                    _datetime(record['kickoff_at'], 'kickoff') != _datetime(original['kickoff_at'], 'kickoff')):
                raise ValueError('conflicting repeated verified result requires review')
            duplicates.append({'period': identity[0], 'match_id': identity[1], 'excluded_collection': source,
                               'retained_collection': chosen[identity][2],
                               'reason': 'same_result_earliest_verification_retained'})
            continue
        chosen[identity] = (record, line, source)
    unique = sorted(chosen.values(), key=lambda x: (x[0]['period'], x[0]['seq'], x[0]['match_id']))
    results = [record for record, _, _ in unique]
    settlement = settle_unique(registry_path, registry_sha256=registry_sha256, results=results,
                               evaluated_at=now, root=root)
    queue = plan_pending(registry, settlement, bundles, evaluated_at=now)
    raw = b''.join(line if line.endswith(b'\n') else line + b'\n' for _, line, _ in unique)
    audit = {'executed_at': now, 'registry_sha256': registry_sha256, 'sources': sources,
             'observations_n': len(observations), 'results_n': len(results),
             'duplicate_n': len(duplicates), 'excluded_duplicates': duplicates,
             'output_sha256': digest(raw), 'raw_bytes_and_original_verification_times_preserved': True,
             'result_records_reimported_from_http_originals': True,
             'execution_code_sha256': {name: digest((Path(__file__).resolve().parent / name).read_bytes())
                  for name in ('collection_workflow.py', 'result_collection.py', 'frozen_results.py',
                               'espn_summary.py', 'unique_settlement.py', 'frozen_settlement.py')},
             'refitted': False, 'reselected_after_results': False, 'production_gate_passed': False,
             'independent_source_and_clock_authentication': False}
    # Validate everything before creating any output; never replace older evidence.
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    with (out / 'results.jsonl').open('xb') as stream:
        stream.write(raw)
    for name, value in [('MERGE_AUDIT.json', audit), ('settlement.json', settlement), ('pending_queue.json', queue)]:
        with (out / name).open('x') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
    return {'offered_n': settlement['offered_n'], 'predicted_n': settlement['predicted_n'],
            'paired_n': settlement['paired_n'], 'duplicate_n': len(duplicates),
            'pending_n': queue['pending_n'], 'due_for_poll_n': queue['due_for_poll_n'],
            'next_poll_not_before': queue['next_poll_not_before'], 'brier': settlement['brier_candidate']}


def main():
    ap = argparse.ArgumentParser(description='核验HTTP原件、唯一合并赛果、生成北单待采集清单')
    ap.add_argument('--registry', required=True)
    ap.add_argument('--registry-sha256', required=True)
    ap.add_argument('--collection', nargs=2, action='append', required=True, metavar=('FOLDER', 'INDEX_SHA256'))
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    print(json.dumps(merge_collections(args.registry, args.registry_sha256, args.collection, args.out)))


if __name__ == '__main__':
    main()
