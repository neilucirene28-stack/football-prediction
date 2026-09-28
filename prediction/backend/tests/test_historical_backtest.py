import copy
import importlib.util
import json
import pathlib
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'tools_historical_backtest.py'
spec=importlib.util.spec_from_file_location('audit',SCRIPT)
audit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
ROOT=pathlib.Path(__file__).resolve().parents[2]
FIXTURE=ROOT/'fixture_snapshot.json'

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=json.loads(FIXTURE.read_text(encoding='utf-8'))
    def test_isolation_and_counts(self):
        pre,outcome,report=audit.build(self.fixture)
        self.assertEqual(report['main_records'],11)
        self.assertEqual(report['unattributed_additional_records'],34)
        self.assertEqual(report['history_records_before_snapshot'],201)
        self.assertEqual(len(report['history_quarantined']),0)
        self.assertEqual(outcome['full_time'],{'home':1,'away':1})
        self.assertNotIn('全场比分',json.dumps(pre,ensure_ascii=False))
        self.assertNotIn('outcome',pre)
        self.assertNotIn('result',pre)
        self.assertEqual(report['status'],'historical_audit_only_not_model_backtest')
    def test_post_cutoff_history_quarantined(self):
        d=copy.deepcopy(self.fixture)
        d['盘口历史']['保留原始字段记录'][0]['时间']='2026-09-20T20:30:00'
        pre,_,report=audit.build(d)
        self.assertEqual(len(pre['handicap_history_through_cutoff']),200)
        self.assertEqual(len(report['history_quarantined']),1)
    def test_rejects_post_kickoff_snapshot(self):
        d=copy.deepcopy(self.fixture)
        d['盘口快照']['采集时间_北京时间']='2026/09/20 23:31:00'
        with self.assertRaises(audit.AuditError): audit.build(d)
    def test_rejects_corrupted_counts(self):
        d=copy.deepcopy(self.fixture);d['盘口快照']['原始公司盘口记录数']=44
        with self.assertRaises(audit.AuditError): audit.build(d)
    def test_output_split_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            p=pathlib.Path(root)/'fixture.json';p.write_text(json.dumps(self.fixture,ensure_ascii=False),encoding='utf-8')
            target=pathlib.Path(root)/'out'
            audit.write_report(str(p),str(target))
            self.assertNotIn('full_time',(target/'prematch_only.json').read_text())
            self.assertEqual(json.loads((target/'outcome_only.json').read_text())['total_goals'],2)
            with self.assertRaises(audit.AuditError): audit.write_report(str(p),str(target))

if __name__=='__main__': unittest.main()
