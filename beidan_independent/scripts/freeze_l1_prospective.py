"""Freeze current real pool for prospective L1 walk-forward research.

No match outcome is available yet. Unknown identities stay blocked in the
complete pool; no artificial historic source clocks or score labels are added.
"""
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from beidan_bd1.walk_forward import run_walk_forward
from beidan_bd1.frozen_settlement import seal_bundle

root=Path(__file__).resolve().parents[1]
now=datetime.now(timezone.utc).isoformat()
pool_path=root/'outputs/20261010_179_shadow.json'
roster=json.loads(pool_path.read_text())['predictions']
history_path=root/'outputs/ger2_native/audit_and_predictions.json'
source=json.loads(history_path.read_text())
history=[r['record'] for r in source['results']]
bindings={r['seq']:r['binding'] for r in source['research_predictions']}
fixtures=[]
for r in roster:
    f={k:r[k] for k in ('period','seq','match_id','sport','home','away','league','kickoff_at')}
    f.update(official_handicap=None,official_handicap_reason='archived_line_not_fresh_verified; no independent pre-cutoff line receipt',
             canonical_identity_approved=False,beidan_fixture_binding_approved=False)
    if r['seq'] in bindings:
        binding=bindings[r['seq']]
        # All clocks come from the actual previously emitted data audit.
        clock={'status':'ok','name':'archived_ESPN_scoreboard_Codex_source_structure_audit',
               'available_at':source['verified_at'],'raw_sha256':binding['raw_sha256'],
               'provider_capture_clock_independently_verified':False}
        f.update(fixture_source=clock,identity_source={**clock,'name':'ESPN_native_team_stage_structure_audit'},
                 provider_home_id=binding['provider_home_id'],provider_away_id=binding['provider_away_id'],
                 provider_league_id=history[0]['provider_league_id'],season_year=2026,season_type=14359,
                 provider_match_id=binding['proposed_provider_event_id'])
    else:
        f.update(fixture_source={'status':'missing','name':'provider_fixture_not_bound','available_at':None},
                 identity_source=None)
    fixtures.append(f)
folds=[{'period':'26103','cutoff_at':now,'expected_total':179,'fixtures':fixtures}]
report=run_walk_forward(history=history,folds=folds,results=[],evaluated_at=now,
        model_family='l1_team_strength',identity_mode='provider_native_espn')
assert report['offered_n']==179 and report['predicted_n']==5 and report['paired_n']==0
assert report['brier_candidate'] is None and report['production_gate_passed'] is False
report['prospective_freeze']={'generated_at':now,'original_roster_sha256':hashlib.sha256(pool_path.read_bytes()).hexdigest(),
     'history_audit_sha256':hashlib.sha256(history_path.read_bytes()).hexdigest(),
     'canonical_identity_approved_n':0,'official_handicap_imported_n':0,
     'shadow_predictions_are_not_betting_recommendations':True}
report['execution_code_sha256']={name:hashlib.sha256((root/'beidan_bd1'/name).read_bytes()).hexdigest()
    for name in ('walk_forward.py','provider_native.py','team_strength.py','baseline.py','mean_preserving.py')}
folder=root/'outputs/l1_prospective';folder.mkdir(exist_ok=True)
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
bundle=folder/stamp;bundle.mkdir()
for name,data in [('history.jsonl',history),('results.jsonl',[])]:
    with (bundle/name).open('x') as fh:
        for row in data:fh.write(json.dumps(row,ensure_ascii=False)+'\n')
(bundle/'folds.json').write_text(json.dumps(folds,ensure_ascii=False,indent=2))
(bundle/'report.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False))
sealed=seal_bundle(bundle)
print(json.dumps({'path':str(bundle),'cutoff_at':now,'full_pool_n':179,'native_candidate_n':5,
      'blocked_n':174,'pending_n':len(report['pending_ids']),'paired_n':0,'brier':None,
      'selected_ridge':report['selections'][0]['selected_ridge'],'production_gate_passed':False,
      'manifest_sha256':sealed['manifest_sha256'],'sealed_at':sealed['sealed_at']}))
