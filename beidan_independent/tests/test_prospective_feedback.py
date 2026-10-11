"""Synthetic feedback boundaries and transferable provenance; no model evidence."""
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from beidan_bd1.evaluation import _brier
from beidan_bd1.frozen_settlement import seal_bundle, load_bundle
from beidan_bd1.prospective_feedback import build_feedback, verify_feedback
from beidan_bd1.unique_settlement import resolve_bundle
from beidan_bd1.walk_forward import RIDGES, run_walk_forward
from test_beidan_frozen_settlement import make_bundle, SEALED
from test_beidan_l1_walk_forward import native_case
from test_unique_settlement import register

CUTOFF='2026-10-09T10:00:00+08:00'

class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.early=self.root/'early';self.early.mkdir()
        original,result,_=make_bundle(self.early)
        self.saved=copy.deepcopy(original['folds'][0]['matches'][0])
        self.fixture=json.loads((self.early/'folds.json').read_bytes())[0]['fixtures'][0]
        self.results=[];fixtures=[];matches=[]
        for i in range(30):
            mid=f'synthetic-{i}'
            fixture={**self.fixture,'match_id':mid,'provider_match_id':f'provider-{i}'}
            fixtures.append(fixture);matches.append({**copy.deepcopy(self.saved),'match_id':mid})
            self.results.append({**copy.deepcopy(result),'match_id':mid,'provider_match_id':fixture['provider_match_id']})
        self.folds=[{'period':self.fixture['period'],'cutoff_at':'2026-10-07T10:00:00+08:00',
                     'expected_total':30,'fixtures':fixtures}]
        original.update(offered_n=30,football_offered_n=30,predicted_n=30,pending_ids=[r['match_id'] for r in fixtures])
        original['folds'][0].update(offered_n=30,football_offered_n=30,predicted_n=30,matches=matches)
        (self.early/'folds.json').write_text(json.dumps(self.folds));(self.early/'report.json').write_text(json.dumps(original))
        (self.early/'freeze_manifest.json').unlink()
        with patch('beidan_bd1.frozen_settlement._utc_now',return_value=SEALED):self.pin=seal_bundle(self.early)['manifest_sha256']
        self.registry=self.root/'registry.json';self.result_path=self.root/'results.jsonl'
        self.registry.write_text(json.dumps(register.build([(str(self.early),self.pin)])))
        self.digest=hashlib.sha256(self.registry.read_bytes()).hexdigest()
        self.history,folds,_=native_case();self.current=folds[-1]

    def feedback(self,results=None,fixtures=None):
        self.result_path.write_text(''.join(json.dumps(r)+'\n' for r in (self.results if results is None else results)))
        return build_feedback(self.registry,self.digest,self.result_path,cutoff_at=CUTOFF,
                              current_fixtures=fixtures or self.current['fixtures'],root=self.root)

    def execute(self,receipt):
        return run_walk_forward(history=self.history,folds=[self.current],results=[],evaluated_at=CUTOFF,
            model_family='l1_team_strength',identity_mode='provider_native_espn',allow_expired_blocked=True,
            prospective_feedback=receipt,feedback_root=self.root)

    def test_threshold_29_cold_and_30_selects_saved_grid_without_refit(self):
        before={p.name:p.read_bytes() for p in self.early.iterdir()}
        with patch('beidan_bd1.walk_forward._team_candidate',side_effect=AssertionError('old forecast refit')):
            cold=self.feedback(self.results[:29]);selected=self.feedback()
        self.assertEqual(cold['selection_n'],29);self.assertIsNone(cold['selected_parameter'])
        self.assertEqual(cold['past_only_brier'],{})
        expected={str(k):_brier(self.saved['alternatives'][str(k)]['vectors']['wdl'],0,0) for k in RIDGES}
        self.assertEqual(selected['selection_n'],30)
        for k,v in expected.items():self.assertAlmostEqual(selected['past_only_brier'][k],v)
        self.assertEqual(selected['selected_parameter'],min(RIDGES,key=lambda k:expected[str(k)]))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.early.iterdir()})
        self.assertFalse(selected['old_forecasts_reselected'])

    def test_future_observation_does_not_push_29_over_threshold(self):
        rows=copy.deepcopy(self.results)
        rows[-1].update(verified_at='2026-10-09T11:00:00+08:00')
        rows[-1]['result_source']['available_at']=rows[-1]['verified_at']
        receipt=self.feedback(rows)
        self.assertEqual(receipt['selection_n'],29)
        self.assertEqual(receipt['excluded_not_yet_available_result_n'],1)
        self.assertIsNone(receipt['selected_parameter'])

    def test_optional_result_available_at_null_uses_actual_verified_source_clock(self):
        rows=[{**r,'result_available_at':None} for r in self.results]
        receipt=self.feedback(rows)
        self.assertEqual(receipt['selection_n'],30)
        self.assertTrue(all(r['result_available_at']==rows[0]['verified_at'] for r in receipt['eligible_rows']))

    def test_own_upcoming_provider_event_excluded_but_expired_blocked_row_can_feed(self):
        upcoming={**self.current['fixtures'][0],'provider_match_id':'provider-0'}
        receipt=self.feedback(fixtures=[upcoming])
        self.assertEqual(receipt['selection_n'],29)
        self.assertEqual(receipt['excluded_current_fixture_keys'],[['pool7','synthetic-0']])
        expired=self.folds[0]['fixtures'][0]
        receipt=self.feedback(fixtures=[self.current['fixtures'][0],expired])
        self.assertEqual(receipt['selection_n'],30)

    def test_actual_executor_and_seal_use_same_receipt_with_empty_new_results(self):
        receipt=self.feedback();report=self.execute(receipt)
        self.assertEqual(report['selections'][0]['selection_n'],30)
        self.assertEqual(report['selections'][0]['selected_parameter'],receipt['selected_parameter'])
        self.assertEqual(report['paired_n'],0)
        self.assertFalse(report['production_gate_passed'])
        self.assertEqual(report['folds'][0]['matches'][0]['selected_parameter'],receipt['selected_parameter'])
        self.new=self.root/'new';self.new.mkdir()
        report['executed_at']='2026-10-09T10:00:01+08:00';report['prospective_freeze']={'generated_at':CUTOFF}
        (self.new/'history.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.history))
        (self.new/'results.jsonl').write_text('');(self.new/'folds.json').write_text(json.dumps([self.current]))
        (self.new/'report.json').write_text(json.dumps(report))
        with patch('beidan_bd1.frozen_settlement._utc_now',return_value=datetime.fromisoformat('2026-10-09T10:00:02+08:00')):
            pin=seal_bundle(self.new)['manifest_sha256']
        loaded,_,_=load_bundle(self.new,manifest_sha256=pin,evaluated_at='2026-10-09T12:00:00+08:00')
        self.assertEqual(loaded['selection_feedback'],receipt)
        report['selections'][0]['selection_n']=31
        (self.new/'report.json').write_text(json.dumps(report));(self.new/'freeze_manifest.json').unlink()
        with self.assertRaisesRegex(ValueError,'selection differs'):seal_bundle(self.new)

    def test_tampered_receipt_loss_count_or_bytes_rejected(self):
        receipt=self.feedback()
        for change in ('loss','count','result','digest'):
            altered=copy.deepcopy(receipt)
            if change=='loss':altered['past_only_brier']['5.0']=0.
            if change=='count':altered['selection_n']=500
            if change=='result':altered['results_original_utf8']+='\n'
            if change=='digest':altered['registry_sha256']='0'*64
            with self.assertRaises(ValueError):verify_feedback(altered,cutoff_at=CUTOFF,current_fixtures=self.current['fixtures'],root=self.root)

    def test_duplicate_results_and_conflicting_native_ids_rejected(self):
        with self.assertRaises(ValueError):self.feedback(self.results+[self.results[0]])
        rows=copy.deepcopy(self.results);rows[0]['provider_home_id']='wrong'
        with self.assertRaises(ValueError):self.feedback(rows)

    def test_later_versions_never_increase_count_or_change_original_alternatives(self):
        late=self.root/'late';late.mkdir()
        for p in self.early.iterdir():
            if p.name!='freeze_manifest.json':(late/p.name).write_bytes(p.read_bytes())
        report=json.loads((late/'report.json').read_bytes());report['executed_at']='2026-10-07T10:00:03+08:00'
        for row in report['folds'][0]['matches']:
            row['alternatives']['5.0']=copy.deepcopy(row['alternatives']['None'])
        (late/'report.json').write_text(json.dumps(report))
        with patch('beidan_bd1.frozen_settlement._utc_now',return_value=datetime.fromisoformat('2026-10-07T10:00:04+08:00')):
            latepin=seal_bundle(late)['manifest_sha256']
        before=self.feedback()
        self.registry.write_text(json.dumps(register.build([(str(late),latepin),(str(self.early),self.pin)])))
        self.digest=hashlib.sha256(self.registry.read_bytes()).hexdigest();after=self.feedback()
        self.assertEqual(after['selection_n'],30)
        self.assertEqual(before['past_only_brier'],after['past_only_brier'])
        self.assertTrue(all(r['bundle']==str(self.early) for r in after['eligible_rows']))

    def test_changed_clock_and_lowered_threshold_are_not_accepted(self):
        receipt=self.feedback();altered=copy.deepcopy(receipt);altered['min_selection_matches']=1
        with self.assertRaises(ValueError):self.execute(altered)
        with self.assertRaisesRegex(ValueError,'clock differs'):
            verify_feedback(receipt,cutoff_at='2026-10-09T10:00:01+08:00',current_fixtures=self.current['fixtures'],root=self.root)

    def test_transfer_root_wins_over_still_existing_original_checkout(self):
        old=self.root/'old/beidan_independent/outputs/l1_prospective/early';old.mkdir(parents=True)
        new=self.root/'new/beidan_independent';new.mkdir(parents=True)
        self.assertEqual(resolve_bundle(str(old),new),new/'outputs/l1_prospective/early')
        self.assertFalse(resolve_bundle(str(old),new).exists())

    def test_empty_results_keep_cold_prior_and_prior_archive_must_be_strictly_earlier(self):
        receipt=self.feedback([])
        self.assertEqual(receipt['selection_n'],0)
        self.assertEqual(receipt['past_only_brier'],{})
        self.assertIsNone(receipt['selected_parameter'])
        with self.assertRaisesRegex(ValueError,'sealed before'):
            build_feedback(self.registry,self.digest,self.result_path,cutoff_at=SEALED.isoformat(),
                           current_fixtures=self.current['fixtures'],root=self.root)

    def test_equal_candidate_scores_favour_prior_at_threshold(self):
        report=json.loads((self.early/'report.json').read_bytes())
        for row in report['folds'][0]['matches']:
            row['alternatives']={str(k):copy.deepcopy(row['baseline']) for k in RIDGES}
        (self.early/'report.json').write_text(json.dumps(report));(self.early/'freeze_manifest.json').unlink()
        with patch('beidan_bd1.frozen_settlement._utc_now',return_value=SEALED):
            self.pin=seal_bundle(self.early)['manifest_sha256']
        self.registry.write_text(json.dumps(register.build([(str(self.early),self.pin)])))
        self.digest=hashlib.sha256(self.registry.read_bytes()).hexdigest()
        receipt=self.feedback()
        self.assertEqual(receipt['selection_n'],30)
        self.assertIsNone(receipt['selected_parameter'])
