#!/usr/bin/env python3
"""从已有竞彩账本JSONL导出球队情报特征；不连数据库，不覆盖输入。"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.jingcai_team_features import extract_rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.input.resolve()==args.output.resolve():parser.error('不能覆盖输入')
    rows=[json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    report=extract_rows(rows,asof=datetime.now(timezone.utc))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as handle:
        os.chmod(args.output,0o600);handle.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'input_rows':len(rows),'exported_rows':len(report['feature_rows']),
                      'excluded':report['excluded'],'production_effect_enabled':False},ensure_ascii=False))


if __name__=='__main__':main()
