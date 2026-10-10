#!/usr/bin/env python3
"""离线读取竞彩已封存预测+结算导出的JSONL，验证牌数增量；不连接数据库。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.jingcai_cards_validation import evaluate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--test-start",required=True,help="预先固定的带时区测试起点，不做自动择优")
    args=parser.parse_args()
    if args.input.resolve()==args.output.resolve():parser.error("输出不能覆盖输入账本")
    rows=[json.loads(line) for line in args.input.read_text().splitlines() if line.strip()]
    report=evaluate(rows,asof=datetime.now(timezone.utc),test_start=args.test_start)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"input_rows":report["input_rows"],
                      "eligible_matches":report["eligible_matches"],"production_enabled":False},ensure_ascii=False))
    return 0


if __name__=="__main__":raise SystemExit(main())
