import json
from pathlib import Path
import unittest
from beidan_bd1.provider_native import predict_espn_native

class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p=next((Path(__file__).resolve().parents[1]/'outputs/espn_summary_audit').glob('audit_*.json'))
        cls.rows=[r['record'] for r in json.loads(p.read_text())['results']]
    def predict(self, rows, **kwargs):
        return predict_espn_native(rows,asof_at='2026-10-09T12:00:00Z',kickoff_at='2026-10-10T12:00:00Z',
            home_id='7476',away_id='7477',league_id=self.rows[0]['provider_league_id'],
            season_year=2026,season_type=14287,handicap=-1,**kwargs)
    def test_real_30_halves_enable_native_research_without_join_approval(self):
        p=self.predict(self.rows)
        self.assertEqual(p['training_n'],30)
        self.assertFalse(p['production_eligible']);self.assertFalse(p['beidan_fixture_binding_approved'])
        self.assertEqual(len(p['vectors']),6)
        for v in p['vectors'].values():self.assertAlmostEqual(sum(v.values()),1,places=8)
    def test_missing_summary_or_late_verification_cannot_train(self):
        bad=[{**r,'summary_sha256':None} for r in self.rows]
        with self.assertRaises(ValueError):self.predict(bad)
        late=[{**r,'verified_at':'2026-10-10T00:00:00Z'} for r in self.rows]
        with self.assertRaises(ValueError):self.predict(late)
