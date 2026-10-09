import copy
import hashlib
import json
import unittest
from beidan_bd1.af_history import audit_history

class AFHistoryTests(unittest.TestCase):
    def fixture(self):
        e={'fixture': {'id':1,'date':'2026-09-01T12:00:00Z','timestamp':1788264000,'status':{'short':'FT'}},
           'league':{'id':79,'season':2026},'teams':{'home':{'id':1,'name':'A'},'away':{'id':2,'name':'B'}},
           'goals':{'home':2,'away':1},'score':{'fulltime':{'home':2,'away':1},'halftime':{'home':1,'away':0},
           'extratime':{'home':None,'away':None},'penalty':{'home':None,'away':None}}}
        return {'get':'fixtures','parameters':{'league':'79','season':'2026','status':'FT'},'errors':[],
                'paging':{'current':1,'total':1},'results':1,'response':[e]}
    def run_audit(self,p):
        raw=json.dumps(p).encode()
        receipt={'start_utc':'2026-10-09T11:00:00Z','end_utc':'2026-10-09T11:00:02Z',
                 'http_status':200,'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'params':p['parameters']}
        return audit_history(raw,receipt,verified_at='2026-10-09T11:01:00Z',league_id=79,season=2026)
    def test_native_does_not_approve_chinese_identity_or_backdate(self):
        rows,r=self.run_audit(self.fixture())
        self.assertEqual(r['explicit_halftime_n'],1)
        self.assertFalse(rows[0]['identity_verified'])
        self.assertFalse(rows[0]['human_reviewed'])
        self.assertIsNone(rows[0]['result_available_at'])
        self.assertEqual(rows[0]['verified_at'],'2026-10-09T11:01:00Z')
    def test_missing_half_is_not_invented(self):
        p=self.fixture();p['response'][0]['score']['halftime']['home']=None
        rows,r=self.run_audit(p)
        self.assertIsNone(rows[0]['ht_home']);self.assertEqual(r['explicit_halftime_n'],0)
    def test_reject_wrong_stage_penalty_or_incomplete_page(self):
        for mutation in ('season','status','paging','half','duplicate'):
            p=self.fixture()
            if mutation=='season':p['response'][0]['league']['season']=2025
            if mutation=='status':p['response'][0]['fixture']['status']['short']='PEN'
            if mutation=='paging':p['paging']['total']=2
            if mutation=='half':p['response'][0]['score']['halftime']['home']=3
            if mutation=='duplicate':p['response'].append(copy.deepcopy(p['response'][0]));p['results']=2
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.run_audit(p)
    def test_native_fit_keeps_research_status_and_all_six_vectors(self):
        from beidan_bd1.af_history import predict_native_shadow
        p=self.fixture();base=p['response'][0]
        p['response']=[copy.deepcopy(base) for _ in range(40)]
        for i,e in enumerate(p['response']):
            e['fixture']['id']=i+1
            if i%2:e['teams']['home'],e['teams']['away']=e['teams']['away'],e['teams']['home']
        p['results']=40;raw=json.dumps(p).encode()
        receipt={'start_utc':'2026-10-09T11:00:00Z','end_utc':'2026-10-09T11:00:02Z',
                 'http_status':200,'size_bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'params':p['parameters']}
        future=copy.deepcopy(base);future['fixture'].update(id=99,date='2026-10-10T12:00:00Z',status={'short':'NS'})
        future['fixture']['timestamp']=1791633600
        future['goals']={'home':None,'away':None}
        future['score']={k:{'home':None,'away':None} for k in ('halftime','fulltime','extratime','penalty')}
        result=predict_native_shadow(raw,receipt,verified_at='2026-10-09T11:01:00Z',
                  asof_at='2026-10-09T11:02:00Z',fixture=future,handicap=-1)
        self.assertEqual(result['training_n'],40)
        self.assertEqual(len(result['vectors']),6)
        for v in result['vectors'].values():self.assertAlmostEqual(sum(v.values()),1,places=8)
        self.assertFalse(result['production_eligible'])
        self.assertFalse(result['canonical_identity_approved'])
        self.assertFalse(result['beidan_fixture_binding_approved'])
        with self.assertRaises(ValueError):
            predict_native_shadow(raw,receipt,verified_at='2026-10-09T11:01:00Z',
                  asof_at='2026-10-09T10:59:00Z',fixture=future)
