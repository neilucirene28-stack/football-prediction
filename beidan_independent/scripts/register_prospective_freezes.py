"""Register earliest sealed predictions; never count later versions as new games."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from beidan_bd1.frozen_settlement import load_bundle


def build(entries):
    chosen, bundles = {}, []
    now = datetime.now(timezone.utc).isoformat()
    loaded = []
    for path, digest in entries:
        report, pool, predictions = load_bundle(path, manifest_sha256=digest, evaluated_at=now)
        manifest = json.loads((Path(path) / 'freeze_manifest.json').read_bytes())
        loaded.append((manifest['sealed_at'], path, digest, pool, predictions))
    duplicates = 0
    for sealed, path, digest, pool, predictions in sorted(loaded):
        bundles.append({'path':path, 'manifest_sha256':digest, 'sealed_at':sealed, 'predicted_n':len(predictions)})
        for mid in predictions:
            fixture = pool[mid]
            key = (fixture['period'], mid)
            physical = [fixture[k] for k in ('provider_match_id','provider_home_id','provider_away_id',
                                             'provider_league_id','season_year','season_type','kickoff_at')]
            if key in chosen:
                if chosen[key]['physical_identity'] != physical:
                    raise ValueError('same pool identity has conflicting frozen provider bindings')
                duplicates += 1
                continue
            chosen[key] = {'period':key[0], 'match_id':mid, 'bundle':path, 'manifest_sha256':digest,
                           'sealed_at':sealed, 'physical_identity':physical}
    # Do not let duplicate pool labels multiply the same provider fixture.
    provider_events = [row['physical_identity'][0] for row in chosen.values()]
    if len(provider_events) != len(set(provider_events)):
        raise ValueError('provider fixture appears under multiple pool identities')
    return {'policy':'earliest_qualified_sealed_forecast_per_unique_fixture', 'created_at':now,
            'bundles':bundles, 'unique_predictions_n':len(chosen), 'later_duplicate_predictions_excluded_n':duplicates,
            'rows':list(chosen.values()), 'paired_n':0, 'brier':None,
            'production_gate_passed':False, 'parameter_selection_after_results':False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', action='append', nargs=2, required=True, metavar=('PATH','SHA256'))
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    registry = build(args.bundle)
    with Path(args.out).open('x') as fh: json.dump(registry, fh, ensure_ascii=False, indent=2)
    print(json.dumps({k:registry[k] for k in ('unique_predictions_n','later_duplicate_predictions_excluded_n','paired_n')}))


if __name__ == '__main__': main()
