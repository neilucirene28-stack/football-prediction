"""Preserve successful and failed response bodies in verified lossless parts."""
import argparse
import json
from beidan_bd1.http_batch_archive import archive_http_batch

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--index-name', default='VERIFIED_FILE_INDEX.json')
    args = ap.parse_args()
    print(json.dumps(archive_http_batch(args.folder, index_name=args.index_name)))
