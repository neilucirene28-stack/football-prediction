"""原封存输入的只读兼容与候选比较；不会生成历史赛前记录。"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def worker(project,records,mode,frozen):
    sys.path.insert(0,str(project))
    from engine.jingcai_predictor import predict
    cfg=json.loads(frozen.read_text())['config']
    out=[]
    for row in json.loads(records.read_text())['results']:
        config=dict(row.get('config') or {})
        if mode!='default':config.update(cfg)
        if mode=='handicap_shadow':config['handicap_market_weight']=.5
        r=predict(row['input'],config,asof=datetime.fromisoformat(row['prediction']['evaluation_asof']))
        assert r['evaluation_mode']=='historical_replay'
        out.append({'number':row['number'],'prediction':r})
    print(json.dumps(out,ensure_ascii=False))


def main(args):
    data=json.loads(args.records.read_text());assert data['project']=='jingcai'
    files=[args.records,*sorted((args.records.parent/'sealed').glob('*.json'))]
    hashes={str(p):sha(p) for p in files};current=Path(__file__).resolve().parents[1]
    modes={}
    for project,mode in ((args.reference,'default'),(current,'default'),
                         (current,'selected'),(current,'handicap_shadow')):
        process=subprocess.run([sys.executable,__file__,'--worker',str(project),
            str(args.records),mode,str(args.frozen)],capture_output=True,text=True,check=True)
        key='v13_reference' if project==args.reference else 'v14_'+mode
        modes[key]=json.loads(process.stdout)
    outcomes=json.loads(args.results.read_text()) if args.results else []
    if isinstance(outcomes,dict):outcomes=outcomes['matches']
    resultmap={r['number']:r for r in outcomes}
    sys.path.insert(0,str(current))
    from scripts.scoreboard import score_one
    out=[]
    for i,row in enumerate(data['results']):
        seal=args.records.parent/'sealed'/f"{row['number'][-3:]}-{row['match_id']}.json"
        assert sha(seal)==row['sealed_record_sha256']
        assert json.loads(seal.read_text())['prediction']==row['prediction']
        for name in ('v13_reference','v14_default'):
            for field in ('score_matrix_full','p_final_full','lambda_home_full','lambda_away_full'):
                assert modes[name][i]['prediction'][field]==row['prediction'][field],(row['number'],field)
        predictions={name:rs[i]['prediction'] for name,rs in modes.items()}
        rec={'number':row['number'],'sealed_sha256':row['sealed_record_sha256'],
            'modes':predictions,'research_scoring':{}}
        actual=resultmap.get(row['number']) or resultmap.get(row['number'][-3:])
        rec['observed_result']=actual
        if actual and actual.get('hg') is not None and actual.get('ag') is not None:
            actual=dict(actual,home_goals=actual['hg'],away_goals=actual['ag'],
                        ht_home=actual.get('hthg'),ht_away=actual.get('htag'))
            for name,r in predictions.items():
                rec['research_scoring'][name]=score_one(r,actual)
        out.append(rec)
    assert hashes=={str(p):sha(p) for p in files}
    report={'project':'jingcai','computed_at':datetime.now(timezone.utc).isoformat(),
        'mode':'historical_replay_only','default_matches_original_22_full_matrices':True,
        'original_files_unchanged':hashes,'selected_config':json.loads(args.frozen.read_text()),
        'handicap_shadow_weight':.5,'handicap_shadow_weight_fitted':False,
        'qualification':'current results not used for selection; no claim of independent accuracy gain',
        'records':out}
    args.output.parent.mkdir(exist_ok=True,parents=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'records':len(out),'default_matches_original':True,
        'source_files_unchanged':len(files),'scored':sum(bool(r['research_scoring']) for r in out)}))


if __name__=='__main__':
    if len(sys.argv)==6 and sys.argv[1]=='--worker':
        worker(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],Path(sys.argv[5]))
    else:
        p=argparse.ArgumentParser(description=__doc__)
        p.add_argument('--records',type=Path,required=True);p.add_argument('--reference',type=Path,required=True)
        p.add_argument('--frozen',type=Path,required=True);p.add_argument('--results',type=Path)
        p.add_argument('--output',type=Path,required=True)
        main(p.parse_args())
