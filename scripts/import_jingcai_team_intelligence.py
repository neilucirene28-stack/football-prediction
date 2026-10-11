#!/usr/bin/env python3
"""离线验证球队情报及来源原件字节；不抓网、不改旧预测、不补造采集时刻。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.jingcai_inputs import aware_datetime
from engine.jingcai_team_intelligence import build_intelligence


def import_intelligence(payload, intelligence, evidence_files):
    if not isinstance(payload,dict) or not isinstance(intelligence,dict) or not isinstance(evidence_files,dict):
        raise ValueError('输入必须为JSON对象')
    evidence=intelligence.get('evidence')
    if not isinstance(evidence,list) or any(not isinstance(e,dict) or not isinstance(e.get('id'),str) for e in evidence):
        raise ValueError('缺来源证据')
    if set(evidence_files)!=set(e.get('id') for e in evidence):
        raise ValueError('原件路径须与证据ID一一对应')
    for e in evidence:
        raw=Path(evidence_files[e['id']]).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=e.get('sha256'):
            raise ValueError('来源原件字节摘要不匹配')
    candidate={**payload,'team_intelligence':intelligence}
    result=build_intelligence(candidate,aware_datetime(payload.get('snapshot_at'),'snapshot_at'))
    return candidate,{'status':result['status'],'evidence_byte_hashes_checked':len(evidence),
                      'probability_effect_enabled':False,
                      'qualification':'byte hashes checked, collection time and source meaning remain caller declared; '
                                      'payload enrichment only, not a new sealed forecast'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload',type=Path,required=True)
    parser.add_argument('--intelligence',type=Path,required=True)
    parser.add_argument('--evidence-files',type=Path,required=True,help='JSON对象：证据ID→本地HTTP原件路径')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve() in [p.resolve() for p in (args.payload,args.intelligence,args.evidence_files)]:
        parser.error('不能覆盖输入')
    candidate,report=import_intelligence(json.loads(args.payload.read_text()),
        json.loads(args.intelligence.read_text()),json.loads(args.evidence_files.read_text()))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as handle:
        os.chmod(args.output,0o600)
        handle.write(json.dumps(candidate,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__': main()
