"""Replay newly retained official source receipts without network requests."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from beidan_bd1.artifact_io import read_artifact
from beidan_bd1.jleague_official import extract_fixture
from beidan_bd1.kleague_official import extract_fixtures

ROOT = Path(__file__).resolve().parents[1]


def audit():
    receipts, fixtures = [], []
    for folder in sorted((ROOT/'data_sample').glob('official_*_20261010_v29')):
        saved = json.loads((folder/'RECEIPT.json').read_bytes())
        for receipt in saved.get('receipts', [saved]):
            raw = read_artifact(folder/receipt['local_file'])
            if len(raw) != receipt['bytes'] or hashlib.sha256(raw).hexdigest() != receipt['sha256']:
                raise ValueError('received original differs from source receipt')
            start = datetime.fromisoformat(receipt['start_utc'])
            end = datetime.fromisoformat(receipt['end_utc'])
            if not start <= end <= datetime.now(timezone.utc):
                raise ValueError('source receipt clock invalid')
            proof = {**receipt, 'receipt_folder': str(folder.relative_to(ROOT))}
            receipts.append(proof)
            if receipt['http_status'] != 200:
                continue
            url = receipt['url']
            rows = []
            if '/match/j2/2026/1010' in url:
                rows = [extract_fixture(raw, source_url=url, expected_sha256=receipt['sha256'])]
            elif url == 'https://www.kleague.com/getScheduleList.do':
                body = receipt['request_body']
                body = json.loads(body) if isinstance(body, str) else body
                rows = extract_fixtures(raw, expected_sha256=receipt['sha256'], league_id=body['leagueId'],
                                        year=int(body['year']), month=int(body['month']))
            for row in rows:
                fixtures.append({**row, 'available_at': receipt['end_utc'], 'source_url': url,
                                 'receipt_folder': proof['receipt_folder']})
    j = [r for r in fixtures if r['provider'] == 'jleague_official']
    k = [r for r in fixtures if r['provider'] == 'kleague_official']
    today = [r for r in fixtures if r['kickoff_at'].startswith('2026-10-10')]
    return {'audited_at': datetime.now(timezone.utc).isoformat(), 'receipts': receipts,
            'jleague_native_fixture_n': len(j), 'kleague_month_fixture_n': len(k),
            'today_native_fixture_n': len(today), 'fixtures': fixtures,
            'official_beidan_handicap_sp_import_n': 0, 'approved_beidan_binding_n': 0,
            'new_qualified_forecast_n': 0, 'production_gate_passed': False,
            'history_note': 'J2前一场有FT但当前结构未取得显式HT；韩国月赛程没有HT。均未进入训练。',
            'beidan_note': '政府资料指向www.bjlot.com.cn；本次原件HTTP502，未取得官方字段。'}


if __name__ == '__main__':
    output = ROOT/'outputs/v29_official_resource_audit.json'
    with output.open('x') as stream:
        json.dump(audit(), stream, ensure_ascii=False, indent=2)
    print(output)
