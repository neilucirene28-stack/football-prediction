#!/usr/bin/env python3
"""只读导出一个竞彩版本的封存预测与结算；不打印连接串，不修改账本。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.jingcai_review import load_review_rows


def export_rows(conn, *, version, days, asof, output):
    if not isinstance(version,str) or not version.strip() or version=='unknown':
        raise ValueError("必须明确指定一个真实竞彩模型版本")
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        rows=load_review_rows(conn,days=days,asof=asof,version=version)
    # x模式不覆盖旧导出；文件可能有私有原输入，应留在授权测试环境。
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as handle:
        os.chmod(output,0o600)
        for row in rows:handle.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
    return {'scope':'jingcai_only','model_version':version,'exported_at':asof.isoformat(),
            'rows':len(rows),'mode':'read_only_no_ledger_changes'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--version',required=True)
    parser.add_argument('--days',type=int,default=365)
    args=parser.parse_args()
    url=os.environ.get('DATABASE_URL')
    if not url:parser.error('未配置竞彩DATABASE_URL')
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url,row_factory=dict_row) as conn:
            report=export_rows(conn,version=args.version,days=args.days,
                               asof=datetime.now(timezone.utc),output=args.output)
    except ImportError:parser.error('测试环境需安装psycopg')
    except Exception:parser.error('竞彩只读导出失败；检查版本、测试库权限、依赖或输出文件是否已存在')
    print(json.dumps(report,ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
