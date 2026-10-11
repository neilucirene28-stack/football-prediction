"""Archived evidence integrity tests; mutated cases are not performance data."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from beidan_bd1.artifact_io import pack_artifact
from beidan_bd1.collection_workflow import audit_collection, digest, merge_collections, plan_pending
from beidan_bd1.frozen_settlement import load_bundle

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / 'outputs'
REGISTRY = OUTPUTS / 'v22_prospective_registry.json'
REGISTRY_SHA = '3b45be0123dfa0156dc48ae24907b6aaa594e2030dac60f3591f3c64b14176b3'
SOURCE = OUTPUTS / 'v23_result_collection_v19_20261010_morning'
OTHER = OUTPUTS / 'v23_result_collection_v22_ned2_20261010_morning'


class CollectionAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        index = json.loads((SOURCE / 'COLLECTION_INDEX.json').read_bytes())
        cls.key = (index['bundle'], index['manifest_sha256'])
        cls.bundles = {cls.key: load_bundle(ROOT / cls.key[0], manifest_sha256=cls.key[1],
                         evaluated_at=datetime.now(timezone.utc).isoformat())}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / 'copy'
        shutil.copytree(SOURCE, self.folder)

    def update_index(self, change):
        path = self.folder / 'COLLECTION_INDEX.json'
        value = json.loads(path.read_bytes())
        change(value)
        path.write_text(json.dumps(value))

    def call(self, expected=None):
        return audit_collection(self.folder, expected or digest((self.folder / 'COLLECTION_INDEX.json').read_bytes()),
              bundles=self.bundles, evaluated_at=datetime.now(timezone.utc).isoformat())

    def test_archive_reimports_all_records_with_original_times_and_lines(self):
        rows, evidence = self.call()
        self.assertEqual(evidence['results_n'], 10)
        self.assertEqual(evidence['pool_ledger_n'], 179)
        self.assertEqual(b''.join(r[1] for r in rows), (SOURCE / 'results.jsonl').read_bytes())

    def test_index_pin_and_raw_body_changes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'index digest'): self.call('0' * 64)
        index = json.loads((self.folder / 'COLLECTION_INDEX.json').read_bytes())
        path = self.folder / index['receipts'][0]['local_file']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'HTTP original'): self.call()

    def test_forged_result_is_rejected_even_if_index_hash_matches(self):
        path = self.folder / 'results.jsonl'
        records = [json.loads(line) for line in path.read_bytes().splitlines()]
        records[0]['ft_home'] += 1
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
        with self.assertRaisesRegex(ValueError, 'reimported'): self.call()

    def test_url_clock_and_path_tampering_are_rejected(self):
        original = (self.folder / 'COLLECTION_INDEX.json').read_bytes()
        changes = [lambda i: i['receipts'][0].update(url='https://example.com/another-event'),
                   lambda i: i['receipts'][0].update(end_utc='2026-10-08T00:00:00Z'),
                   lambda i: i['receipts'][0].update(local_file='../outside.json'),
                   lambda i: i.update(completed_at='2099-01-01T00:00:00Z')]
        for change in changes:
            with self.subTest(change=change):
                (self.folder / 'COLLECTION_INDEX.json').write_bytes(original)
                self.update_index(change)
                with self.assertRaises(ValueError): self.call()

    def test_missing_pool_rows_duplicate_receipts_and_unregistered_freeze_rejected(self):
        original = (self.folder / 'COLLECTION_INDEX.json').read_bytes()
        changes = [lambda i: i['complete_pool_ledger'].pop(),
                   lambda i: i['receipts'].append(copy.deepcopy(i['receipts'][0])),
                   lambda i: i.update(manifest_sha256='0' * 64),
                   lambda i: i.update(collection_scope_match_ids=['not-predicted'])]
        for change in changes:
            with self.subTest(change=change):
                (self.folder / 'COLLECTION_INDEX.json').write_bytes(original)
                self.update_index(change)
                with self.assertRaises(ValueError): self.call()

    def test_verified_result_cannot_come_from_failed_http_status(self):
        self.update_index(lambda i: i['receipts'][0].update(http_status=403))
        with self.assertRaisesRegex(ValueError, 'successful HTTP'): self.call()

    def test_verification_cannot_predate_received_original(self):
        path = self.folder / 'results.jsonl'
        records = [json.loads(line) for line in path.read_bytes().splitlines()]
        records[0]['verified_at'] = '2026-10-09T00:00:00Z'
        path.write_text(''.join(json.dumps(r) + '\n' for r in records))
        with self.assertRaisesRegex(ValueError, 'verification predates'): self.call()

    def test_lossless_parts_retain_the_original_result(self):
        index = json.loads((self.folder / 'COLLECTION_INDEX.json').read_bytes())
        path = self.folder / index['receipts'][0]['local_file']
        pack_artifact(path, compress=True)
        path.unlink()
        rows, evidence = self.call()
        self.assertEqual(evidence['results_n'], 10)
        self.assertEqual(rows[0][0]['summary_sha256'], index['receipts'][0]['sha256'])

    def test_failed_http_receipts_remain_pending_without_manufactured_results(self):
        def reject_all(index):
            for receipt in index['receipts']:
                receipt.update(http_status=403, error='unsuccessful HTTP status')
            for row in index['complete_pool_ledger']:
                if row['status'] == 'verified_regular_time_ft':
                    row['status'] = 'pending_or_rejected'
        self.update_index(reject_all)
        (self.folder / 'results.jsonl').write_bytes(b'')
        rows, evidence = self.call()
        self.assertEqual(rows, [])
        self.assertEqual(evidence['receipts_n'], 10)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'out'

    def call(self, sources):
        specs = [(folder, digest((folder / 'COLLECTION_INDEX.json').read_bytes())) for folder in sources]
        return merge_collections(REGISTRY, REGISTRY_SHA, specs, self.out, root=ROOT)

    def test_real_archives_reproduce_existing_unique_settlement_without_fitting(self):
        with patch('beidan_bd1.walk_forward._team_candidate', side_effect=AssertionError('refit')):
            value = self.call([SOURCE, OTHER])
        self.assertEqual((value['offered_n'], value['predicted_n'], value['paired_n']), (179, 66, 19))
        self.assertEqual((self.out / 'results.jsonl').read_bytes(),
                         (OUTPUTS / 'v23_unique_verified_results_20261010_morning.jsonl').read_bytes())
        self.assertEqual(value['brier'], 0.20622960801176707)
        queue = json.loads((self.out / 'pending_queue.json').read_bytes())
        self.assertEqual(len(queue['rows']), 179)
        self.assertEqual(sum(r['status'] == 'originally_uncovered' for r in queue['rows']), 113)

    def test_repeated_collection_is_counted_once_and_never_overwrites_output(self):
        other = Path(self.tmp.name) / 'another-copy'
        shutil.copytree(SOURCE, other)
        value = self.call([SOURCE, other])
        self.assertEqual((value['paired_n'], value['duplicate_n']), (10, 10))
        self.assertEqual((self.out / 'results.jsonl').read_bytes(), (SOURCE / 'results.jsonl').read_bytes())
        before = (self.out / 'MERGE_AUDIT.json').read_bytes()
        with self.assertRaises(FileExistsError): self.call([SOURCE])
        self.assertEqual((self.out / 'MERGE_AUDIT.json').read_bytes(), before)

    def test_empty_or_repeated_directory_does_not_write_any_output(self):
        for sources in ([], [SOURCE, SOURCE]):
            with self.subTest(sources=sources), self.assertRaises(ValueError): self.call(sources)
        self.assertFalse(self.out.exists())

    def test_conflicting_verified_results_fail_before_writing(self):
        record = json.loads((SOURCE / 'results.jsonl').read_bytes().splitlines()[0])
        altered = {**record, 'ft_home': record['ft_home'] + 1}
        calls = [([(record, b'first\n', 'a')], {}), ([(altered, b'other\n', 'b')], {})]
        with patch('beidan_bd1.collection_workflow.audit_collection', side_effect=calls):
            with self.assertRaisesRegex(ValueError, 'conflicting repeated'):
                self.call([SOURCE, OTHER])
        self.assertFalse(self.out.exists())

    def test_queue_uses_earliest_owner_only_and_does_not_claim_full_time(self):
        registry = {'rows': [{'period': 'test', 'match_id': 'a', 'bundle': 'earliest', 'manifest_sha256': 'hash'},
                             {'period': 'test', 'match_id': 'b', 'bundle': 'earliest', 'manifest_sha256': 'hash'}]}
        bundles = {('earliest', 'hash'): ({}, {
            mid: {'kickoff_at': '2026-10-10T13:00:00+08:00', 'provider_match_id': 'event-' + mid}
            for mid in ('a', 'b')}, {})}
        settlement = {'pending_ids': ['a'], 'paired_n': 1, 'matches': [
            {'period': 'test', 'match_id': mid, 'status': 'settled_frozen_probabilities' if mid == 'b'
             else 'pending_verified_result'} for mid in ('a', 'b', 'blocked')]}
        before = plan_pending(registry, settlement, bundles, evaluated_at='2026-10-10T06:44:59Z')
        self.assertEqual(before['due_for_poll_n'], 0)
        after = plan_pending(registry, settlement, bundles, evaluated_at='2026-10-10T06:45:00Z')
        self.assertEqual(after['collection_groups'], [{'bundle': 'earliest', 'manifest_sha256': 'hash', 'match_ids': ['a']}])
        self.assertTrue(after['poll_delay_is_not_full_time_evidence'])

    def test_same_local_id_in_different_periods_is_not_collapsed(self):
        registry = {'rows': [{'period': period, 'match_id': 'same', 'bundle': period, 'manifest_sha256': 'hash'}
                             for period in ('p1', 'p2')]}
        bundles = {(period, 'hash'): ({}, {'same': {'kickoff_at': '2026-10-10T13:00:00+08:00',
                          'provider_match_id': period}}, {}) for period in ('p1', 'p2')}
        settlement = {'pending_ids': ['same'], 'paired_n': 1, 'matches': [
            {'period': 'p1', 'match_id': 'same', 'status': 'settled_frozen_probabilities'},
            {'period': 'p2', 'match_id': 'same', 'status': 'pending_verified_result'}]}
        queue = plan_pending(registry, settlement, bundles, evaluated_at='2026-10-10T06:45:00Z')
        self.assertEqual((queue['settled_n'], queue['pending_n'], queue['due_for_poll_n']), (1, 1, 1))
        self.assertEqual(queue['collection_groups'][0]['bundle'], 'p2')


if __name__ == '__main__':
    unittest.main()
