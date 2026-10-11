#!/usr/bin/env python3
"""离线检查竞彩净胜球校准；不连接数据库、不写生产参数。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.jingcai_margin_validation import evaluate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--test-start',required=True)
    args=parser.parse_args()
    if args.input.resolve()==args.output.resolve(): parser.error('输出不能覆盖输入账本')
    rows=[json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    report=evaluate(rows,asof=datetime.now(timezone.utc),test_start=args.test_start)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','input_rows','eligible_matches','production_enabled')}))


if __name__=='__main__': main()
