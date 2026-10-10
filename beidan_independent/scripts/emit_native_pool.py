"""Complete-pool shadow output with explicit information quality per fixture."""
import argparse
from datetime import datetime,timezone
import json,math
from pathlib import Path
from beidan_bd1.native_pool import collect_batches
from beidan_bd1.baseline import predict_l3
from beidan_bd1.history_import import load_verified_history
root=Path(__file__).resolve().parents[1]
now=datetime.now(timezone.utc).isoformat()
old=json.loads((root/'outputs/20261010_179_shadow.json').read_text())
ap=argparse.ArgumentParser();ap.add_argument('audits',nargs='+');ap.add_argument('--output',required=True);args=ap.parse_args()
batches=[json.loads(Path(p).read_text()) for p in args.audits]
_,bindings,research=collect_batches(batches,cutoff_at=now)
history=load_verified_history(root/'data_sample/history.jsonl');rows=[];priors={}
for r in old['predictions']:
    line=r['handicap']
    if r['seq'] in research:
        p=research[r['seq']]['prediction'];quality='source_native_team_strength_binding_unapproved'
    else:
        if line not in priors:
            priors[line]=predict_l3(history,asof_at=now,kickoff_at=r['kickoff_at'],competition_family=None,handicap=line)
        p=priors[line];quality='low_information_root_prior_no_verified_team_history'
    vectors=p['vectors']
    assert all(abs(math.fsum(v.values())-1)<1e-8 for v in vectors.values())
    top=sorted(vectors['score'].items(),key=lambda x:-x[1])[:5]
    rows.append({**{k:r[k] for k in ('period','seq','match_id','sport','league','home','away','kickoff_at','handicap')},
      'generated_at':now,'quality':quality,'production_eligible':False,'parameters_unvalidated':True,
      'beidan_fixture_binding_approved':False,'official_handicap_status':'archived_line_not_fresh_verified',
      'sp_consumed':False,'source_observed_at':None,'asof_backtest_eligible':False,
      'forecast_asof_at':p.get('asof_at',now),'direction':max(vectors['wdl'],key=vectors['wdl'].get),
      'handicap_direction':max(vectors['handicap_wdl'],key=vectors['handicap_wdl'].get),
      'top5':top,'training_n':p['training_n'],'route':p['route'],
      'total_goal_mean':math.fsum(sum(map(int,k.split('-')))*v for k,v in vectors['score'].items()),
      'vectors':vectors,'score_31':p['score_31'],
      'fixture_binding_evidence':research.get(r['seq'],{}).get('binding')})
assert len(rows)==179 and {r['seq'] for r in rows}==set(range(11,190))
report={'generated_at':now,'period':'26103','date':'2026-10-10','timezone':'Asia/Shanghai',
 'model_status':'research_only','production_eligible':False,'football_n':179,
 'native_team_strength_n':len(research),'low_information_prior_n':179-len(research),
 'sp_consumed_n':0,'canonical_binding_approved_n':0,'brier_validation_passed':False,
 'mean_preserving_calibration_active':False,'calibration_parameters':'identity; no market odds',
 'predictions':rows}
out=root/args.output;out.mkdir(exist_ok=True)
(out/'179_probability_vectors.json').write_text(json.dumps(report,ensure_ascii=False,allow_nan=False))
lines=['# 北单26103期明日179场研究输出','',
       f'{len(research)}场来源ID球队强度候选；{179-len(research)}场低信息根先验。全部未经walk-forward/Brier批准，中文绑定未批准，SP未进入模型。',
       '让球来自旧档案，未重新核验。低信息先验不代表已分析各队，不能据此生成投注建议。','',
       '| 场次 | 对阵 | 胜/平/负 | 首选 | 首选比分 | 信息状态 |',
       '|---|---|---|---|---|---|']
for r in rows:
    v=r['vectors']['wdl'];probs='/'.join(f'{v[k]:.1%}' for k in ('胜','平','负'))
    quality='球队强度候选，绑定待核验' if r['seq'] in research else '低信息先验'
    lines.append(f"| {r['seq']:03d} | {r['home']}—{r['away']} | {probs} | {r['direction']} | {r['top5'][0][0]} | {quality} |")
(out/'179研究清单.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='predictions'},ensure_ascii=False))
