"""One locked, auditable result-collection cycle for an external timer."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path

from .collection_workflow import merge_collections
from .result_collection import collect


DENIED = {400, 401, 403, 404, 429}


def select_groups(queue, collections):
    """Do not poll settled/not-due rows or repeat a retained access denial."""
    denied = set()
    for folder, expected in collections:
        raw = (Path(folder) / 'COLLECTION_INDEX.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('collection journal digest differs')
        for receipt in json.loads(raw)['receipts']:
            if receipt.get('http_status') in DENIED:
                denied.add(receipt['match_id'])
    groups = []
    for group in queue['collection_groups']:
        ids = [mid for mid in group['match_ids'] if mid not in denied]
        if ids:
            groups.append({**group, 'match_ids': ids})
    return groups, sorted(denied)


def run_cycle(config_path, state_dir):
    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        raise ValueError('run from the beidan_independent checkout root')
    config_raw = Path(config_path).read_bytes()
    config = json.loads(config_raw)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'LOCK').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = state_dir / 'CONFIG_SHA256'
        digest = hashlib.sha256(config_raw).hexdigest()
        if pin.exists():
            if pin.read_text().strip() != digest:
                raise ValueError('use a new state directory for a changed registry/config')
        else:
            pin.write_text(digest + '\n')
        collections = [(row['folder'], row['index_sha256']) for row in config['collections']]
        # Completed captures survive interruption before the final merge. Re-audit
        # all recovered indexes and raw bodies; never trust cached result totals.
        for index in sorted(state_dir.glob('*/capture_*/COLLECTION_INDEX.json')):
            collections.append((str(index.parent), hashlib.sha256(index.read_bytes()).hexdigest()))
        run = state_dir / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        run.mkdir(exist_ok=False)
        merge_collections(config['registry'], config['registry_sha256'], collections, run / 'before', root=root)
        queue = json.loads((run / 'before/pending_queue.json').read_bytes())
        groups, denied = select_groups(queue, collections)
        for number, group in enumerate(groups):
            folder = run / f'capture_{number:03d}'
            collect(group['bundle'], group['manifest_sha256'], folder,
                    only_match_ids=group['match_ids'])
            index = folder / 'COLLECTION_INDEX.json'
            collections.append((str(folder), hashlib.sha256(index.read_bytes()).hexdigest()))
        after = merge_collections(config['registry'], config['registry_sha256'], collections, run / 'after', root=root)
        receipt = {'completed_at': datetime.now(timezone.utc).isoformat(),
                   'config_sha256': digest, 'requested_groups_n': len(groups),
                   'access_denied_match_ids': denied, **after,
                   'production_gate_passed': False, 'refitted': False}
        (run / 'RUN_RECEIPT.json').write_text(json.dumps(receipt, indent=2))
        return receipt


def main():
    parser = argparse.ArgumentParser(description='执行一次北单赛果采集；由外部定时器调用')
    parser.add_argument('--config', required=True)
    parser.add_argument('--state-dir', required=True)
    args = parser.parse_args()
    print(json.dumps(run_cycle(args.config, args.state_dir)))


if __name__ == '__main__':
    main()
