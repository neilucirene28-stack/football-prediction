"""只读按版本核查平局首选、概率总量、精度与召回率。"""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.jingcai_draw import class_audit


def main(args):
    if args.output and args.output.resolve() in {p.resolve() for p in (args.predictions,args.results) if p}:
        raise ValueError('不得覆盖原预测或原赛果')
    raw=args.predictions.read_bytes();data=json.loads(raw)
    records=data.get('results',data.get('records')) if isinstance(data,dict) else data
    if not isinstance(records,list):raise ValueError('输入须包含results/records列表')
    actuals={};result_hash=None
    if args.results:
        result_bytes=args.results.read_bytes();rows=json.loads(result_bytes);result_hash=hashlib.sha256(result_bytes).hexdigest()
        if isinstance(rows,dict):rows=rows['matches']
        actuals={r['number']:r for r in rows}
    groups=defaultdict(list)
    for row in records:
        prediction=row['modes'][args.mode] if args.mode else row['prediction']
        if prediction.get('model')!='jingcai':raise ValueError('拒绝混入其他项目')
        actual=actuals.get(row['number']) or row.get('observed_result')
        label=None
        if actual and actual.get('hg') is not None and actual.get('ag') is not None:
            h,a=actual['hg'],actual['ag']
            if any(isinstance(v,bool) or not isinstance(v,int) or v<0 for v in (h,a)):
                raise ValueError('最终进球必须为非负整数或未知')
            label=0 if h>a else 1 if h==a else 2
        groups[prediction['model_version']].append({'probs_full':prediction['p_final_full'],'outcome':label})
    out={'project':'jingcai','evaluated_at':datetime.now(timezone.utc).isoformat(),
        'evaluation_mode':'readonly_audit','prediction_sha256':hashlib.sha256(raw).hexdigest(),
        'result_sha256':result_hash,'by_version':{version:class_audit(rows) for version,rows in groups.items()},
        'qualification':'declared/source screenshot results not authenticated here; never changes forecasts or parameters'}
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps(out,ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--predictions',type=Path,required=True);p.add_argument('--results',type=Path)
    p.add_argument('--mode');p.add_argument('--output',type=Path)
    main(p.parse_args())
