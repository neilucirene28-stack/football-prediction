#!/usr/bin/env python3
"""Read-only audit of Jingcai local forecast archives; never rewrites snapshots."""
import argparse
import json
import os
import sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from engine.jingcai_archive import verify_archive


def audit_archive(directory):
    root=Path(directory);accepted=[];rejected=[]
    for folder in sorted(root.iterdir()) if root.exists() else []:
        if not folder.is_dir():continue
        try:
            forecast,receipt=verify_archive(folder)
            accepted.append({'forecast_id':forecast['forecast_id'],
                'model_version':forecast['result']['model_version'],
                'kickoff_at':forecast['input']['kickoff_at'],'durable_at':receipt['durable_at'],
                'sha256':receipt['sha256']})
        except (OSError,ValueError,KeyError,TypeError) as exc:
            rejected.append({'folder':folder.name,'reason':type(exc).__name__})
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'eligible_local_archives':len(accepted),
            'rejected_archives':len(rejected),'accepted':accepted,'rejected':rejected,
            'independent_source_provenance_verified':False,
            'note':'仅核对本地文件摘要与生成/落盘时间；不证明赔率或伤停来源在赛前真实可用。'}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,
        default=os.environ.get('JINGCAI_ARCHIVE_DIR',str(ROOT/'data/snapshots/jingcai')))
    args=parser.parse_args();print(json.dumps(audit_archive(args.directory),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
