"""Freeze current real pool for prospective L1 walk-forward research.

Earlier authenticated frozen outcomes can select future research parameters.
Unknown identities stay blocked; current outcomes never enter the new test.
"""
import argparse
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from beidan_bd1.native_pool import collect_batches
from beidan_bd1.walk_forward import run_walk_forward
from beidan_bd1.frozen_settlement import seal_bundle
from beidan_bd1.snapshot import _datetime
from beidan_bd1.artifact_io import read_artifact
from beidan_bd1.prospective_feedback import build_feedback

root=Path(__file__).resolve().parents[1]
now=datetime.now(timezone.utc).isoformat()
pool_path=root/'outputs/20261010_179_shadow.json'
roster=json.loads(pool_path.read_text())['predictions']
ap=argparse.ArgumentParser();ap.add_argument('audits',nargs='+')
ap.add_argument('--feedback-registry');ap.add_argument('--feedback-registry-sha256');ap.add_argument('--feedback-results')
args=ap.parse_args()
feedback_args=(args.feedback_registry,args.feedback_registry_sha256,args.feedback_results)
if any(feedback_args) and not all(feedback_args):
    ap.error('feedback requires registry, independently retained registry SHA256 and verified results together')
audit_paths=[Path(p) for p in args.audits]
batches=[json.loads(read_artifact(p)) for p in audit_paths]
history,bindings,research=collect_batches(batches,cutoff_at=now)
fixtures=[]
for r in roster:
    f={k:r[k] for k in ('period','seq','match_id','sport','home','away','league','kickoff_at')}
    f.update(official_handicap=None,official_handicap_reason='archived_line_not_fresh_verified; no independent pre-cutoff line receipt',
             canonical_identity_approved=False,beidan_fixture_binding_approved=False)
    if r['seq'] in bindings:
        metadata=bindings[r['seq']]
        binding=metadata['binding']
        # All clocks come from the actual previously emitted data audit.
        clock={'status':'ok','name':'archived_ESPN_scoreboard_Codex_source_structure_audit',
               'available_at':metadata['available_at'],'raw_sha256':binding['raw_sha256'],
               'provider_capture_clock_independently_verified':False}
        f.update(fixture_source=clock,identity_source={**clock,'name':'ESPN_native_team_stage_structure_audit'},
                 provider_home_id=binding['provider_home_id'],provider_away_id=binding['provider_away_id'],
                 provider_league_id=metadata['provider_league_id'],season_year=metadata['season_year'],season_type=metadata['season_type'],
                 provider_match_id=binding['proposed_provider_event_id'])
    else:
        f.update(fixture_source={'status':'missing','name':'provider_fixture_not_bound','available_at':None},
                 identity_source=None)
    fixtures.append(f)
folds=[{'period':'26103','cutoff_at':now,'expected_total':179,'fixtures':fixtures}]
feedback=(build_feedback(args.feedback_registry,args.feedback_registry_sha256,args.feedback_results,
        cutoff_at=now,current_fixtures=fixtures,root=root) if all(feedback_args) else None)
report=run_walk_forward(history=history,folds=folds,results=[],evaluated_at=now,
        model_family='l1_team_strength',identity_mode='provider_native_espn',allow_expired_blocked=True,
        prospective_feedback=feedback,feedback_root=root)
future_research_n=sum(_datetime(bindings[seq]['binding']['kickoff_at'],'kickoff') > _datetime(now,'now') for seq in research)
if (report['offered_n']!=179 or report['predicted_n']!=future_research_n or report['paired_n']!=0
        or report['brier_candidate'] is not None or report['production_gate_passed'] is not False):
    raise ValueError('prospective freeze coverage or pending-only gate differs')
report['prospective_freeze']={'generated_at':now,'original_roster_sha256':hashlib.sha256(pool_path.read_bytes()).hexdigest(),
     'history_audits_sha256':{str(p):hashlib.sha256(read_artifact(p)).hexdigest() for p in audit_paths},
     'canonical_identity_approved_n':0,'official_handicap_imported_n':0,
     'shadow_predictions_are_not_betting_recommendations':True}
report['execution_code_sha256']={name:hashlib.sha256((root/'beidan_bd1'/name).read_bytes()).hexdigest()
    for name in ('walk_forward.py','provider_native.py','team_strength.py','baseline.py','mean_preserving.py','native_pool.py',
                 'prospective_feedback.py','unique_settlement.py','frozen_settlement.py')}
folder=root/'outputs/l1_prospective';folder.mkdir(exist_ok=True)
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
bundle=folder/stamp;bundle.mkdir()
for name,data in [('history.jsonl',history),('results.jsonl',[])]:
    with (bundle/name).open('x') as fh:
        for row in data:fh.write(json.dumps(row,ensure_ascii=False)+'\n')
(bundle/'folds.json').write_text(json.dumps(folds,ensure_ascii=False,indent=2))
(bundle/'report.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False))
sealed=seal_bundle(bundle)
print(json.dumps({'path':str(bundle),'cutoff_at':now,'full_pool_n':179,'native_candidate_n':report['predicted_n'],
      'blocked_n':179-report['predicted_n'],'pending_n':len(report['pending_ids']),'paired_n':0,'brier':None,
      'selected_ridge':report['selections'][0]['selected_ridge'],'production_gate_passed':False,
      'feedback_available_paired_n':feedback['available_paired_n'] if feedback is not None else None,
      'feedback_selection_n':feedback['selection_n'] if feedback is not None else None,
      'manifest_sha256':sealed['manifest_sha256'],'sealed_at':sealed['sealed_at']}))
