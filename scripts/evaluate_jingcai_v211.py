#!/usr/bin/env python3
"""Offline research replay, chronological draw-calibration holdout and screenshot review.

No HTTP, DB, production parameter writes or retroactive prospective timestamps.
Schedule odds lack independently authenticated availability timestamps, so results
are historical research only, even though form dates are filtered before cutoff.
"""
import argparse
import hashlib
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(rows, gamma=1.0):
    losses = []; draws = []; direction = []; probs = []
    for r in rows:
        p = adjust(r['p'], gamma); y = r['outcome']
        losses.append(-math.log(max(p[y],1e-12)))
        draws.append((p[1]-int(y==1))**2)
        probs.append((p,y))
        direction.append(int(max(range(3),key=lambda i:p[i])==y))
    n = len(rows)
    if not n: return {'n':0}
    return {'n':n, 'brier':sum(sum((p[i]-int(y==i))**2 for i in range(3))/3 for p,y in probs)/n,
            'logloss':sum(losses)/n, 'draw_brier':sum(draws)/n,
            'direction_accuracy':sum(direction)/n,
            'mean_draw_probability':sum(p[1] for p,_ in probs)/n,
            'actual_draw_rate':sum(y==1 for _,y in probs)/n}


def adjust(p,gamma):
    q=[p[0],p[1]*gamma,p[2]]; total=sum(q)
    return [v/total for v in q]


def recent(raw, cutoff):
    out=[]; excluded=0
    for r in sorted(raw,key=lambda r:r.get('date',''),reverse=True):
        if not r.get('date') or r['date'] >= cutoff:
            excluded+=1; continue
        venue=r.get('venue')
        if venue not in ('home','away') or r.get('hg') is None or r.get('ag') is None:
            excluded+=1; continue
        home=venue=='home'
        out.append({'gf':int(r['hg'] if home else r['ag']),
                    'ga':int(r['ag'] if home else r['hg']),
                    'venue':'H' if home else 'A','date':r['date'],'comp':r.get('league','')})
    return out,excluded


def replay(predict, PredictError):
    schedule=json.loads((ROOT/'data/phase2_matches.json').read_text())
    history=json.loads((ROOT/'data/phase2_history.json').read_text())
    histories={r['mido']:r for r in history}
    if len(histories)!=len(history): raise ValueError('duplicate history IDs')
    seen=set(); rows=[]; skipped=[]; excluded=0
    for m in sorted(schedule,key=lambda r:(r['kickoff'],r['mido'])):
        mid=m['mido']
        if mid in seen: raise ValueError('duplicate target match IDs')
        seen.add(mid); h=histories.get(mid)
        if not h or h.get('hg') is None or h.get('ag') is None:
            skipped.append({'id':mid,'reason':'missing_history_or_final_score'});continue
        ko=datetime.strptime(m['kickoff'],'%Y-%m-%d %H:%M').replace(tzinfo=timezone(timedelta(hours=8)))
        cutoff=ko-timedelta(hours=3)
        hr,a=recent(h.get('home_form',[]),cutoff.date().isoformat())
        ar,b=recent(h.get('away_form',[]),cutoff.date().isoformat());excluded+=a+b
        average=sum(r['gf']+r['ga'] for r in hr+ar)/len(hr+ar) if hr+ar else 2.7
        odds=m.get('odds')
        odds_dict=dict(zip(('home','draw','away'),odds)) if odds and all(x is not None and x>1 for x in odds) else None
        payload={'home':m['home'],'away':m['away'],'competition':m['league'],
                 'kickoff_at':ko.isoformat(),'snapshot_at':cutoff.isoformat(),
                 'home_recent':hr,'away_recent':ar,'league_avg_goals':average,
                 'odds':odds_dict,'handicap_line':m.get('rq')}
        try: r=predict(payload,asof=ko-timedelta(hours=1))
        except PredictError as exc:
            skipped.append({'id':mid,'reason':str(exc)});continue
        if r['status']!='ok':
            skipped.append({'id':mid,'reason':r['status']});continue
        p=r.get('p_final_full') or [r[k] for k in ('p_home','p_draw','p_away')]
        if abs(sum(p)-1)>5e-4: raise ValueError('invalid WDL vector')
        # Old Jingcai output retained only display probabilities: normalize for research comparison.
        if 'p_final_full' not in r: p=[v/sum(p) for v in p]
        hg,ag=int(h['hg']),int(h['ag'])
        y=0 if hg>ag else 1 if hg==ag else 2
        scores=r['derivatives']['top_scores']
        rows.append({'id':mid,'date':ko.date().isoformat(),'kickoff':ko.isoformat(),'league':m['league'],
                     'home':m['home'],'away':m['away'],'p':p,'outcome':y,'actual_score':f'{hg}-{ag}',
                     'top1_hit':int(scores[0]['score']==f'{hg}-{ag}'),
                     'top3_hit':int(f'{hg}-{ag}' in [x['score'] for x in scores[:3]]),
                     'top5_hit':int(f'{hg}-{ag}' in [x['score'] for x in scores[:5]]),
                     'goals_bias':r['derivatives']['expected_goals']-hg-ag,
                     'has_market':bool(odds_dict),'model_version':r['model_version'],
                     'virtual_cutoff':cutoff.isoformat(), 'actual_replay_timing':r.get('prediction_timing')})
    return rows,{'target_n':len(schedule),'replayed_n':len(rows),'excluded_form_rows':excluded,'skipped':skipped}


def date_bootstrap(rows,gamma):
    groups=defaultdict(list)
    for r in rows:
        p=r['p'];q=adjust(p,gamma); y=r['outcome']
        delta=sum((q[i]-int(i==y))**2-(p[i]-int(i==y))**2 for i in range(3))/3
        groups[r['date']].append(delta)
    dates=sorted(groups);rng=random.Random(211); samples=[]
    for _ in range(2000):
        resampled=[v for _ in dates for v in groups[rng.choice(dates)]]
        samples.append(sum(resampled)/len(resampled))
    samples.sort()
    return {'method':'2000 paired resamples by match-number date, seed 211',
            'date_groups':len(dates),'delta_brier_candidate_minus_baseline':sum(v for values in groups.values() for v in values)/len(rows),
            'ci95':[samples[49],samples[1949]]}


def calibration(rows):
    dates=sorted({r['date'] for r in rows});a=max(1,int(len(dates)*.6));b=max(a+1,int(len(dates)*.8))
    train=[r for r in rows if r['date'] in dates[:a]]
    valid=[r for r in rows if r['date'] in dates[a:b]]
    test=[r for r in rows if r['date'] in dates[b:]]
    grid=[.8,.9,1.,1.1,1.2,1.3]
    trials=[{'gamma':g,**metrics(train,g)} for g in grid]
    selected=min(trials,key=lambda r:(r['logloss'],abs(r['gamma']-1)))['gamma']
    return {'method':'train-only gamma selection; chronological date-group validation and untouched holdout',
            'objective':'multiclass logloss','gamma_grid':grid,'training_trials':trials,'selected_gamma':selected,
            'split_dates':{'train':dates[:a],'validation':dates[a:b],'holdout':dates[b:]},
            'train':{'baseline':metrics(train),'candidate':metrics(train,selected)},
            'validation':{'baseline':metrics(valid),'candidate':metrics(valid,selected)},
            'holdout':{'baseline':metrics(test),'candidate':metrics(test,selected)},
            'paired_holdout_brier':date_bootstrap(test,selected),
            'production_promoted':False,
            'reason':'research-only history and schedule odds lack authenticated pre-match source availability; one dataset is insufficient for prospective promotion'}


def screenshot_review():
    file=ROOT/'data/predictions/2026-10-09-jingcai.json'
    matches=json.loads(file.read_text())['matches']
    scores=[(3,0),(4,2),(0,0),(1,0),(0,2),(2,0),(3,0),(2,2),(2,1),(1,1),(1,1),(1,3)]
    rows=[]
    for m in matches:
        index=int(m['no'][-3:])-1;hg,ag=scores[index]
        r=m.get('result',m);p=r.get('p_final_full') or [r[k] for k in ('p_home','p_draw','p_away')]
        p=[v/sum(p) for v in p]; y=0 if hg>ag else 1 if hg==ag else 2
        top=r['derivatives']['top_scores']
        rows.append({'no':m['no'],'home':m['home'],'away':m['away'],'actual_score':f'{hg}-{ag}',
                     'p':p,'outcome':y,'direction_hit':int(max(range(3),key=lambda i:p[i])==y),
                     'top1_hit':int(top[0]['score']==f'{hg}-{ag}'),
                     'top3_hit':int(f'{hg}-{ag}' in [s['score'] for s in top[:3]]),
                     'top5_hit':int(f'{hg}-{ag}' in [s['score'] for s in top[:5]]),
                     'available_top_scores_n':len(top),'input_payload_available':bool(m.get('payload'))})
    return {'source_prediction_sha256':sha(file),'source_results':'user-provided screenshots IMG_7257.png / IMG_7258.png',
            'label':'observational review of stored engine forecasts; separate from manual chat picks',
            'independently_sealed_prospective_evaluation':False,
            'probability_note':'legacy display-rounded probabilities normalized; per-prediction generation clocks unavailable',
            'metrics':metrics(rows),'top1_hits':sum(r['top1_hit'] for r in rows),
            'top3_hits':sum(r['top3_hit'] for r in rows),
            'top5_hits':sum(r['top5_hit'] for r in rows if r['available_top_scores_n']>=5),
            'top5_eligible_n':sum(r['available_top_scores_n']>=5 for r in rows),'rows':rows}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--engine-root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/validation/jingcai-v211/research-evaluation.json')
    args=parser.parse_args();sys.path.insert(0,str(args.engine_root))
    from engine.predictor import predict,PredictError
    from engine.backtest import calibration_table
    rows,audit=replay(predict,PredictError)
    output={'created_at':datetime.now(timezone.utc).isoformat(),'engine_root':str(args.engine_root),
            'provenance':'retrospective research reconstruction, not original frozen forecasts',
            'source_hashes':{p:sha(ROOT/p) for p in ('data/phase2_history.json','data/phase2_matches.json')},
            'audit':audit,'overall':metrics(rows),'rows':rows,'calibration_experiment':calibration(rows),
            'class_calibration':{name:calibration_table([(tuple(r['p']),r['outcome']) for r in rows],outcome_index=i)
                                 for i,name in enumerate(('home','draw','away'))} if args.engine_root==ROOT else None,
            'screenshot_review':screenshot_review(),
            'score_metrics':{'top1_hits':sum(r['top1_hit'] for r in rows),'top3_hits':sum(r['top3_hit'] for r in rows),
                             'top5_hits':sum(r['top5_hit'] for r in rows),'mean_goals_bias':sum(r['goals_bias'] for r in rows)/len(rows)}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:output[k] for k in ('audit','overall','score_metrics')},ensure_ascii=False))
    print(json.dumps(output['calibration_experiment']['holdout'],ensure_ascii=False))


if __name__=='__main__': main()
