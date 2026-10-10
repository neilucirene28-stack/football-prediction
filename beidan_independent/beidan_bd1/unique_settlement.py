"""Score each event once against its earliest qualified sealed forecast."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .frozen_settlement import load_bundle, settle_bundle, NATIVE_IDS
from .snapshot import _datetime
from .walk_forward import summarize_pairs, summarize_goal_means


def resolve_bundle(name, root):
    path = Path(name)
    if not path.is_absolute():
        return Path(root) / path
    marker = '/beidan_independent/'
    if marker in name:
        # An explicit checkout root must win even when the old checkout still
        # exists, otherwise transfer verification can silently read outside it.
        return Path(root) / name.split(marker, 1)[1]
    if path.exists():
        return path
    raise ValueError('cannot relocate frozen bundle path')


def settle_unique(registry_path, *, registry_sha256, results, evaluated_at, root=None):
    raw = Path(registry_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != registry_sha256:
        raise ValueError('registry digest differs from independently retained digest')
    registry = json.loads(raw)
    if registry.get('policy') != 'earliest_qualified_sealed_forecast_per_unique_fixture':
        raise ValueError('unsupported forecast selection policy')
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    loaded, complete, chosen = [], {}, {}
    bundle_keys = set()
    for entry in registry['bundles']:
        key = (entry['path'], entry['manifest_sha256'])
        if key in bundle_keys:
            raise ValueError('duplicate registry bundle')
        bundle_keys.add(key)
        path = resolve_bundle(entry['path'], root)
        _, pool, predictions = load_bundle(path, manifest_sha256=entry['manifest_sha256'], evaluated_at=evaluated_at)
        manifest = json.loads((path / 'freeze_manifest.json').read_bytes())
        if entry['sealed_at'] != manifest['sealed_at'] or entry['predicted_n'] != len(predictions):
            raise ValueError('registered bundle metadata differs from original freeze')
        loaded.append((_datetime(manifest['sealed_at'], 'sealed_at'), key, path, pool, predictions))
        for mid, fixture in pool.items():
            identity = (fixture['period'], mid)
            original = complete.get(identity)
            if original is not None and (original['sport'] != fixture['sport'] or
                    _datetime(original['kickoff_at'], 'kickoff') != _datetime(fixture['kickoff_at'], 'kickoff')):
                raise ValueError('complete pool fixture differs across versions')
            complete[identity] = fixture
    # Reconstruct earliest selection from authenticated bundles, not registry row order.
    duplicates = 0
    for sealed, key, path, pool, predictions in sorted(loaded, key=lambda x: (x[0], x[1])):
        for mid in predictions:
            fixture = pool[mid]
            identity = (fixture['period'], mid)
            physical = [fixture[k] for k in (*NATIVE_IDS, 'kickoff_at')]
            if identity in chosen:
                if chosen[identity]['physical_identity'] != physical:
                    raise ValueError('provider identity conflicts across forecast versions')
                duplicates += 1
                continue
            chosen[identity] = {'period':fixture['period'], 'match_id':mid, 'bundle':key[0],
                                'manifest_sha256':key[1], 'sealed_at':sealed.isoformat(),
                                'physical_identity':physical}
    # ESPN event IDs are provider identifiers; another stage label is not a new game.
    provider_events = [r['physical_identity'][0] for r in chosen.values()]
    if len(provider_events) != len(set(provider_events)):
        raise ValueError('same provider event appears under multiple pool identities')
    rows = registry['rows']
    supplied = {(r['period'], r['match_id']):r for r in rows}
    if len(supplied) != len(rows) or set(supplied) != set(chosen):
        raise ValueError('registry prediction identities are incomplete or duplicated')
    for identity, expected in chosen.items():
        row = supplied[identity]
        if (any(row.get(k) != expected[k] for k in ('bundle','manifest_sha256','physical_identity')) or
                _datetime(row.get('sealed_at'), 'row.sealed_at') != _datetime(expected['sealed_at'], 'sealed_at')):
            raise ValueError('registry selected a later or conflicting forecast')
    if (registry['unique_predictions_n'] != len(chosen) or
            registry['later_duplicate_predictions_excluded_n'] != duplicates):
        raise ValueError('registry counts differ from frozen archive')
    indexed = {}
    for result in results:
        identity = (result.get('period'), result.get('match_id'))
        if identity not in chosen or identity in indexed:
            raise ValueError('result duplicated or outside uniquely predicted events')
        indexed[identity] = result
    pairs, settled_rows = {}, {}
    for _, key, path, pool, predictions in loaded:
        owned = {identity for identity, row in chosen.items()
                 if (row['bundle'],row['manifest_sha256']) == key}
        subset = [indexed[identity] for identity in owned if identity in indexed]
        scored = settle_bundle(path, manifest_sha256=key[1], results=subset, evaluated_at=evaluated_at)
        for row in scored['paired_scores']:
            identity = (row['period'],row['match_id'])
            if identity in owned:
                pairs[identity] = row
        for row in scored['matches']:
            identity = (row['period'],row['match_id'])
            if identity in owned:
                settled_rows[identity] = {**row, 'original_bundle':key[0], 'original_manifest_sha256':key[1]}
    ledger = []
    for identity, fixture in complete.items():
        ledger.append(settled_rows.get(identity, {'period':identity[0], 'match_id':identity[1],
                      'status':'blocked_in_all_registered_freezes'}))
    values = list(pairs.values())
    football_n = sum(f['sport'] == 'football' for f in complete.values())
    average = lambda field: math.fsum(r[field] for r in values) / len(values) if values else None
    baseline, candidate = average('baseline'), average('candidate')
    return {'label':'unique_earliest_frozen_settlement_no_refit',
            'executed_at':datetime.now(timezone.utc).isoformat(), 'evaluated_at':evaluated_at,
            'registry_sha256':registry_sha256, 'offered_n':len(complete), 'football_offered_n':football_n,
            'execution_code_sha256':{name:hashlib.sha256((Path(__file__).resolve().parent/name).read_bytes()).hexdigest()
                                     for name in ('unique_settlement.py','frozen_settlement.py','walk_forward.py')},
            'predicted_n':len(chosen), 'blocked_n':len(complete)-len(chosen), 'paired_n':len(values),
            'pending_ids':[mid for period,mid in chosen if (period,mid) not in pairs],
            'later_duplicate_predictions_excluded_n':duplicates,
            'prediction_coverage':len(chosen)/football_n if football_n else 0.,
            'settled_scoring_coverage':len(values)/football_n if football_n else 0.,
            'brier_baseline':baseline, 'brier_candidate':candidate,
            'delta_brier':candidate-baseline if values else None,
            'score31_logloss_baseline':average('score31_logloss_baseline'),
            'score31_logloss_candidate':average('score31_logloss_candidate'),
            'paired_scores':values, 'matches':ledger,
            'numerical_gate':summarize_pairs(values, football_offered_n=football_n, predicted_n=len(chosen)),
            'goal_mean_gate':summarize_goal_means(values), 'refitted':False,
            'reselected_after_results':False, 'production_gate_passed':False,
            'limitations':['digests_do_not_authenticate_sources_or_clocks','canonical_bindings_unapproved',
                          'complete_official_pool_requires_independent_audit']}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--registry', required=True)
    ap.add_argument('--registry-sha256', required=True)
    ap.add_argument('--results', required=True, help='verified JSONL; duplicate results rejected')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    results = [json.loads(line) for line in Path(args.results).read_bytes().splitlines() if line.strip()]
    report = settle_unique(args.registry, registry_sha256=args.registry_sha256, results=results,
                           evaluated_at=datetime.now(timezone.utc).isoformat())
    with Path(args.out).open('x') as fh: json.dump(report, fh, ensure_ascii=False, allow_nan=False, indent=2)
    print(json.dumps({k:report[k] for k in ('offered_n','predicted_n','blocked_n','paired_n','brier_candidate')}))


if __name__ == '__main__': main()
