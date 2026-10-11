import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from beidan_bd1.jleague_official import extract_fixture
from beidan_bd1.kleague_official import extract_fixtures
from beidan_bd1.scheduled_collection import select_groups
from beidan_bd1.artifact_io import read_artifact
from beidan_bd1.result_collection import frozen_league_slugs

ROOT = Path(__file__).resolve().parents[1]


class OfficialResourcesTests(unittest.TestCase):
    def test_transfer_never_reads_original_checkout_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'new/beidan_independent';old=Path(tmp)/'old/beidan_independent'
            (root/'beidan_bd1').mkdir(parents=True);old.mkdir(parents=True)
            content=json.dumps({'research_predictions':[{'binding':{'provider_league_id':'1','provider_league_slug':'new.1'}}]}).encode()
            (root/'audit.json').write_bytes(content)
            (old/'audit.json').write_text('{}')
            report={'prospective_freeze':{'history_audits_sha256':{str(old/'audit.json'):hashlib.sha256(content).hexdigest()}}}
            with patch('beidan_bd1.result_collection.__file__',str(root/'beidan_bd1/result_collection.py')):
                self.assertEqual(frozen_league_slugs(report),{'1':'new.1'})
                (root/'audit.json').unlink()
                with self.assertRaises(FileNotFoundError):frozen_league_slugs(report)

    def test_jleague_explicit_native_fixture(self):
        folder = ROOT / 'data_sample/official_resource_links_20261010_v29'
        receipt = next(r for r in json.loads((folder/'RECEIPT.json').read_bytes())['receipts'] if '/match/j2/' in r['url'])
        raw = read_artifact(folder/receipt['local_file'])
        row = extract_fixture(raw, source_url=receipt['url'], expected_sha256=receipt['sha256'])
        self.assertEqual((row['home_team_id'], row['away_team_id']), ('199', '31219'))
        self.assertEqual(row['kickoff_at'], '2026-10-10T05:00:00+00:00')
        self.assertFalse(row['eligible_for_training'])
        with self.assertRaises(ValueError):
            extract_fixture(raw+b'x', source_url=receipt['url'], expected_sha256=receipt['sha256'])
        with self.assertRaises(ValueError):
            extract_fixture(raw, source_url=receipt['url'].replace('jleague.jp', 'example.com'), expected_sha256=receipt['sha256'])

    def test_lineup_placeholder_cannot_be_fixture(self):
        payload = '1:' + json.dumps({'homeTeam':{'teamId':'1'}, 'awayTeam':{'teamId':'2'}, 'matchTime':'前半0分'}) + '\n'
        raw = ('<script>self.__next_f.push('+json.dumps([1,payload])+')</script>').encode()
        with self.assertRaises(ValueError):
            extract_fixture(raw, source_url='https://www.jleague.jp/match/j2/2026/101013/', expected_sha256=hashlib.sha256(raw).hexdigest())

    def test_kleague_timezone_and_no_prematch_zero_results(self):
        folder=ROOT/'data_sample/official_kleague_api_20261010_v29'
        raw=read_artifact(folder/'schedule.json'); sha=hashlib.sha256(raw).hexdigest()
        rows=extract_fixtures(raw,expected_sha256=sha,league_id=1,year=2026,month=10)
        row=next(r for r in rows if r['provider_match_id']=='2026:1:184')
        self.assertEqual(row['kickoff_at'],'2026-10-10T05:00:00+00:00')
        self.assertFalse(row['eligible_for_training']);self.assertNotIn('ft_home',row)
        with self.assertRaises(ValueError):
            extract_fixtures(raw,expected_sha256=sha,league_id=2,year=2026,month=10)
        value=json.loads(raw);value['data']['scheduleList'].append(copy.deepcopy(value['data']['scheduleList'][0]))
        raw=json.dumps(value).encode()
        with self.assertRaises(ValueError):
            extract_fixtures(raw,expected_sha256=hashlib.sha256(raw).hexdigest(),league_id=1,year=2026,month=10)

    def test_poll_scope_excludes_retained_denial(self):
        queue={'collection_groups':[{'bundle':'b','manifest_sha256':'s','match_ids':['denied','due']}]}
        with tempfile.TemporaryDirectory() as tmp:
            index=Path(tmp)/'COLLECTION_INDEX.json'
            raw=json.dumps({'receipts':[{'match_id':'denied','http_status':403}]}).encode();index.write_bytes(raw)
            groups, denied=select_groups(queue,[(tmp,hashlib.sha256(raw).hexdigest())])
            self.assertEqual(groups[0]['match_ids'],['due']);self.assertEqual(denied,['denied'])
            self.assertEqual(select_groups({'collection_groups':[]},[]),([],[]))
            with self.assertRaises(ValueError):select_groups(queue,[(tmp,'0'*64)])
