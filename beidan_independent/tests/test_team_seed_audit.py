"""Synthetic source-scope corruption cases; no sporting outcomes are fabricated."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from beidan_bd1.team_seed_audit import audit_team_seeds


class TeamSeedAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def payload(a, b, stage=2):
            return {'leagues': [{'slug': 'test.1'}], 'events': [
                {'season': {'year': 2026, 'type': stage}, 'competitions': [{'competitors': [
                 {'homeAway': 'home', 'team': {'id': a}}, {'homeAway': 'away', 'team': {'id': b}}]}]}]}
        raw = json.dumps(payload('1', '2')).encode()
        (self.root/'old.json').write_bytes(raw)
        self.old = {'file': 'old.json', 'sha256': hashlib.sha256(raw).hexdigest()}
        raw = json.dumps(payload('3', '4')).encode()
        self.name = 'scoreboard_test.1_20261011.json'
        (self.root/self.name).write_bytes(raw)
        self.receipt = {'league_slug': 'test.1', 'local_file': self.name,
            'url': 'https://site.api.espn.com/apis/site/v2/sports/soccer/test.1/scoreboard?dates=20261011',
            'start_utc': '2026-10-10T00:00:00Z', 'end_utc': '2026-10-10T00:00:01Z',
            'http_status': 200, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        self.config = {'league_slug': 'test.1', 'season_year': 2026, 'season_type': 2,
            'team_ids': ['1', '2', '3', '4'], 'seed_scoreboard_receipts': [self.old],
            'additional_team_seed_receipt': {'index': 'HTTP_RECEIPTS.json', 'receipt': self.receipt}}

    def call(self):
        (self.root/'HTTP_RECEIPTS.json').write_text(json.dumps({
            'created_at': '2026-10-10T00:00:02Z', 'files': [self.receipt]}))
        return audit_team_seeds(self.config, self.root, verified_at='2026-10-10T00:00:03Z')

    def rewrite(self, payload):
        raw = json.dumps(payload).encode()
        (self.root/self.name).write_bytes(raw)
        self.receipt.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())

    def test_exact_native_scope_and_digest_are_retained(self):
        report = self.call()
        self.assertEqual(report['team_ids'], ['1', '2', '3', '4'])
        self.assertEqual(report['team_n'], 4)
        self.assertFalse(report['canonical_identity_approved'])
        self.assertEqual(len(report['additional_http_index_sha256']), 64)

    def test_changed_body_or_url_rejected(self):
        original = (self.root/self.name).read_bytes()
        (self.root/self.name).write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'original differs'): self.call()
        (self.root/self.name).write_bytes(original)
        self.receipt['url'] = self.receipt['url'].replace('test.1', 'other.1')
        with self.assertRaisesRegex(ValueError, 'URL differs'): self.call()

    def test_foreign_league_or_season_cannot_expand_teams(self):
        payload = json.loads((self.root/self.name).read_bytes())
        payload['leagues'][0]['slug'] = 'other.1'
        self.rewrite(payload)
        with self.assertRaisesRegex(ValueError, 'league differs'): self.call()
        payload['leagues'][0]['slug'] = 'test.1'
        payload['events'][0]['season']['type'] = 999
        self.rewrite(payload)
        with self.assertRaisesRegex(ValueError, 'configured history teams'): self.call()

    def test_unobserved_or_duplicated_team_rejected(self):
        for configured in [['1', '2', '3', '4', '5'], ['1', '2', '3', '4', '4']]:
            self.config['team_ids'] = configured
            with self.assertRaisesRegex(ValueError, 'configured history teams'): self.call()

    def test_reverse_or_future_receipt_clock_rejected(self):
        self.receipt['end_utc'] = '2026-10-09T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'clock order'): self.call()
        self.receipt['end_utc'] = '2026-10-11T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'clock order'): self.call()

    def test_failed_body_verified_but_not_used_to_expand_teams(self):
        self.receipt['http_status'] = 502
        self.config['team_ids'] = ['1', '2']
        self.assertEqual(self.call()['team_n'], 2)
        (self.root/self.name).write_bytes(b'tampered failure')
        with self.assertRaisesRegex(ValueError, 'original differs'): self.call()

    def test_index_mismatch_and_legacy_absence(self):
        self.config['additional_team_seed_receipt']['receipt'] = copy.deepcopy(self.receipt)
        self.config['additional_team_seed_receipt']['receipt']['http_status'] = 201
        with self.assertRaisesRegex(ValueError, 'HTTP index'): self.call()
        self.config.pop('additional_team_seed_receipt')
        self.assertIsNone(audit_team_seeds(self.config, self.root, verified_at='2026-10-10T00:00:03Z'))
