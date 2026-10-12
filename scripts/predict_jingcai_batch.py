"""专用批量竞彩计算；保留未预测清单，绝不以主胜先验补齐场次。"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.jingcai_batch import predict_batch


def main(args):
    if args.output.resolve()==args.input.resolve():raise ValueError('不得覆盖原始输入')
    if args.config and args.output.resolve()==args.config.resolve():raise ValueError('不得覆盖配置')
    if args.output.exists():raise ValueError('输出已存在；请使用新文件名保留历史')
    data=json.loads(args.input.read_text())
    rows=data['records'] if isinstance(data,dict) else data
    cfg=json.loads(args.config.read_text()) if args.config else None
    result=predict_batch(rows,cfg)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'requested_n':result['requested_n'],'predicted_n':result['predicted_n'],
                      'abstained_n':result['abstained_n'],'forecast_top1_counts':
                      result['wdl_classification']['forecast_top1_counts']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True);p.add_argument('--config',type=Path)
    p.add_argument('--output',type=Path,required=True)
    main(p.parse_args())
