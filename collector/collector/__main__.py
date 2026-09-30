"""采集入口：python -m collector --source demo --once"""
import argparse
import os

from .pipeline import get_source, save_matches, save_to_db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=os.environ.get("SOURCE", "demo"))
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    src = get_source(args.source)
    matches = src.validate(src.fetch_matches())
    for m in matches:
        m["_source"] = src.name
    print(f"[{src.name}] 采集到 {len(matches)} 场比赛")

    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            n = save_to_db(matches, db_url)
            print(f"已写入数据库 {n} 条")
            return
        except Exception as e:  # DB 不可用则降级为文件
            print(f"DB 写入失败，降级为文件: {e}")
    path = save_matches(matches)
    print(f"已保存到 {path}")


if __name__ == "__main__":
    main()
