"""独立竞彩JSON入口；asof只用于明确标记的研究重放。"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from engine.jingcai_predictor import predict

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--payload',type=Path,required=True)
    p.add_argument('--config',type=Path)
    p.add_argument('--asof',help='研究重放截止时间；不得作为历史赛前封存')
    args=p.parse_args()
    config=json.loads(args.config.read_text()) if args.config else None
    if config and 'config' in config:
        if config.get('project') != 'jingcai':raise ValueError('只接受竞彩参数文件')
        config=config['config']
    result=predict(json.loads(args.payload.read_text()),config,
        asof=datetime.fromisoformat(args.asof) if args.asof else None)
    print(json.dumps(result,ensure_ascii=False,indent=2))
