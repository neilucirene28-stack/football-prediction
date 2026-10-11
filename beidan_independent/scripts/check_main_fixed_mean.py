"""Main-entry integration qualification against the actual 179-row roster.

Uses no archived SP as ordinary WDL odds. Parameters are predeclared numerical
experiments, not fitted or validated production settings.
"""
from datetime import datetime,timezone
import json,math
from pathlib import Path
from beidan_bd1.baseline import predict_l3
from beidan_bd1.history_import import load_verified_history
from beidan_bd1.probability_chain import shadow_adjust

root=Path(__file__).resolve().parents[1]
roster=json.loads((root/'outputs/20261010_179_shadow.json').read_text())['predictions']
assert len(roster)==179 and {r['seq'] for r in roster}==set(range(11,190))
now=datetime.now(timezone.utc).isoformat()
history=load_verified_history(root/'data_sample/history.jsonl')
records=[]
for line in sorted({r['handicap'] for r in roster}):
    example=next(r for r in roster if r['handicap']==line)
    prior=predict_l3(history,asof_at=now,kickoff_at=example['kickoff_at'],competition_family=None,handicap=line)
    for bias,temp in ((0.,1.),(-.5,.8),(.5,1.2)):
        out=shadow_adjust(prior,asof_at=now,draw_bias=bias,temperature=temp)
        drift=out['goals_after']-out['goals_before']
        assert abs(drift)<1e-9
        assert all(abs(math.fsum(v.values())-1)<1e-8 for v in out['vectors'].values())
        if bias==0:assert out['vectors']==prior['vectors']
        records.append({'line':line,'draw_bias':bias,'temperature':temp,'status':out['status'],
            'prior_mean':out['goals_before'],'candidate_mean':out['goals_after'],'mean_drift':drift,
            'roster_seqs':[r['seq'] for r in roster if r['handicap']==line]})
report={'generated_at':now,'roster_n':179,'distinct_line_n':len({r['handicap'] for r in roster}),
        'candidate_checks_n':len(records),'max_abs_mean_drift':max(abs(r['mean_drift']) for r in records),
        'main_entry':'probability_chain.shadow_adjust','sp_imported_as_ordinary_wdl':False,
        'walk_forward_completed':False,'brier':None,'production_eligible':False,'records':records}
folder=root/'outputs/main_fixed_mean';folder.mkdir(exist_ok=True)
p=folder/'qualification.json';p.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in report.items() if k!='records'}))
