"""Demonstrate that today's historical download cannot enable old forecasts."""
from collections import defaultdict
from datetime import datetime,timezone
import json
from pathlib import Path
from beidan_bd1.walk_forward import run_walk_forward
from beidan_bd1.snapshot import _datetime

root=Path(__file__).resolve().parents[1]
source=json.loads((root/'outputs/ger2_native/audit_and_predictions.json').read_text())
history=[r['record'] for r in source['results']]
groups=defaultdict(list);results=[]
for r in history:
    day=_datetime(r['kickoff_at'],'kickoff').date().isoformat()
    period='provider-day-'+day
    f={k:r[k] for k in ('match_id','kickoff_at','home','away','provider_match_id',
       'provider_home_id','provider_away_id','provider_league_id','season_year','season_type')}
    clock={'status':'ok','name':'historical_result_archive_not_prematch_fixture',
           'available_at':r['verified_at']}
    f.update(period=period,sport='football',fixture_source=clock,identity_source=clock,official_handicap=None)
    groups[day].append(f);results.append({**r,'period':period})
folds=[{'period':'provider-day-'+day,'cutoff_at':day+'T00:00:00Z',
         'expected_total':len(rows),'fixtures':rows} for day,rows in sorted(groups.items())]
report=run_walk_forward(history=history,folds=folds,results=results,
         evaluated_at=datetime.now(timezone.utc).isoformat(),
         model_family='l1_team_strength',identity_mode='provider_native_espn')
assert report['offered_n']==54 and report['predicted_n']==0 and report['paired_n']==0
assert all(f['training_n']==0 for f in report['folds'])
assert report['brier_candidate'] is None and report['production_gate_passed'] is False
report['test_scope']='provider-only reconstructed 54-event scaffold; not full official Beidan history'
report['conclusion']='current archive availability cannot be backdated to historical forecast cutoffs'
folder=root/'outputs/l1_historical_qualification';folder.mkdir(exist_ok=True)
path=folder/'qualification.json';path.write_text(json.dumps(report,ensure_ascii=False,allow_nan=False))
print(json.dumps({'offered_n':54,'fold_n':len(folds),'predicted_n':0,'paired_n':0,'brier':None,
                 'production_gate_passed':False,'conclusion':report['conclusion']}))
