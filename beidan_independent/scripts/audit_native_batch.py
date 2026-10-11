"""Audit retained native HTTP batches and emit prospective research candidates."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from beidan_bd1.espn_fixture import audit_fixture
from beidan_bd1.native_batch_audit import audit_history
from beidan_bd1.provider_native import predict_espn_native
from beidan_bd1.team_seed_audit import audit_team_seeds


def main():
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument('config')
    args = ap.parse_args()
    config = json.loads(Path(args.config).read_bytes())
    code = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [Path(__file__).resolve(), *(root / 'beidan_bd1' / name for name in
                ('native_batch_audit.py', 'espn_schedule.py', 'espn_summary.py', 'espn_fixture.py',
                 'archive_io.py', 'provider_native.py', 'team_strength.py', 'team_seed_audit.py'))]}
    team_seeds = audit_team_seeds(config, root, verified_at=datetime.now(timezone.utc).isoformat())
    report = audit_history(config, root)
    if team_seeds is not None:
        report['history_team_seed_audit'] = team_seeds
    now = report['verified_at']
    roster = {r['seq']: r for r in json.loads((root / 'outputs/20261010_179_shadow.json').read_bytes())['predictions']}
    seeds = {r['file']: r['sha256'] for r in config.get('seed_scoreboard_receipts', [])}
    predictions = []
    for seq, eid, home, away, date in config['proposals']:
        name = f"{config['scoreboard_folder']}/scoreboard_{config['league_slug']}_{date}.json"
        raw = (root / name).read_bytes()
        if seeds and (name not in seeds or hashlib.sha256(raw).hexdigest() != seeds[name]):
            raise ValueError('proposed fixture original differs from retained seed receipt')
        binding = audit_fixture(raw, event_id=eid, league_slug=config['league_slug'],
                    expected_home_id=home, expected_away_id=away, roster=roster[seq], verified_at=now)
        try:
            if (binding['season_year'], binding['season_type']) != (config['season_year'], config['season_type']):
                raise ValueError('fixture season differs from audited history')
            pred = predict_espn_native([r['record'] for r in report['results']], asof_at=now,
                   kickoff_at=binding['kickoff_at'], home_id=home, away_id=away,
                   league_id=binding['provider_league_id'], season_year=config['season_year'],
                   season_type=config['season_type'], handicap=roster[seq]['handicap'])
            pred.update(top5=sorted(pred['vectors']['score'].items(), key=lambda x: -x[1])[:5],
                        direction=max(pred['vectors']['wdl'], key=pred['vectors']['wdl'].get))
            if not all(abs(math.fsum(v.values()) - 1) < 1e-8 for v in pred['vectors'].values()):
                raise ValueError('prediction probability vectors are not normalized')
            predictions.append({'seq': seq, 'home': roster[seq]['home'], 'away': roster[seq]['away'],
                 'binding': binding, 'official_handicap_status': 'archived_line_not_fresh_verified',
                 'status': 'native_research_binding_unapproved', 'prediction': pred})
        except (ValueError, AssertionError) as error:
            predictions.append({'seq': seq, 'status': 'blocked', 'reason': str(error), 'binding': binding})
    report.update(audit_code_sha256=code, canonical_identity_approved_n=0, production_eligible=False,
                  brier=None, walk_forward_eligible_historical_cutoffs=False, research_predictions=predictions,
                  report_completed_at=datetime.now(timezone.utc).isoformat())
    out = root / config['output_folder']
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'audit_and_predictions.json').open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, allow_nan=False)
    print(json.dumps({k: v for k, v in report.items() if k not in ('results', 'research_predictions', 'schedule_checks')}))
    print(json.dumps([{'seq': r['seq'], 'status': r['status'], 'wdl': r.get('prediction', {}).get('vectors', {}).get('wdl'),
            'top5': r.get('prediction', {}).get('top5'), 'reason': r.get('reason')} for r in predictions], ensure_ascii=False))


if __name__ == '__main__':
    main()
