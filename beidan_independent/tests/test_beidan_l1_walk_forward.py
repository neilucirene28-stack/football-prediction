import copy
import unittest
from beidan_bd1.walk_forward import run_walk_forward,summarize_goal_means
from test_beidan_walk_forward import case


def native_case():
    h,f,r=case()
    for row in h:
        row.update(provider_home_id='1',provider_away_id='2',provider_league_id='10',
                   season_year=2026,season_type=123,summary_sha256='0'*64,
                   halftime_source='header.competitions[0].competitors[*].linescores')
    for fold in f:
        row=fold['fixtures'][0]
        row.update(provider_home_id='1',provider_away_id='2',provider_league_id='10',
                   season_year=2026,season_type=123,identity_source=copy.deepcopy(row['fixture_source']))
        row['provider_match_id']=row['match_id']+'-provider'
    for fixture,result in zip((fold['fixtures'][0] for fold in f),r):
        for k in ('provider_match_id','provider_home_id','provider_away_id','provider_league_id','season_year','season_type'):
            result[k]=fixture[k]
    return h,f,r


def run(h,f,r):
    return run_walk_forward(history=h,folds=f,results=r,evaluated_at='2026-10-09T23:00:00+08:00',
        min_selection_matches=1,model_family='l1_team_strength',identity_mode='provider_native_espn')


class L1WalkTests(unittest.TestCase):
    def test_team_refits_train_only_before_cutoff_and_use_earlier_selection(self):
        h,f,r=native_case();out=run(h,f,r)
        self.assertEqual([x['selection_n'] for x in out['selections']],[0,1,2])
        self.assertIsNone(out['selections'][0]['selected_ridge'])
        self.assertEqual(out['declared_candidates'],[None,2.,5.,10.,20.])
        for i,fold in enumerate(out['folds']):
            row=fold['matches'][0]
            self.assertEqual(row['status'],'predicted_research')
            for ridge in ('2.0','5.0','10.0','20.0'):
                p=row['alternatives'][ridge]
                self.assertEqual(p['training_n'],35+i)
                self.assertNotIn(row['match_id'],p['training_ids'])
        self.assertFalse(out['production_gate_passed'])
        self.assertFalse(out['research_qualification_gate'])
    def test_own_or_future_test_score_cannot_change_its_fit_or_selection(self):
        h,f,r=native_case();before=run(h,f,r)
        hh,rr=copy.deepcopy(h),copy.deepcopy(r)
        hh[-2]['ft_home']=rr[1]['ft_home']=8
        after=run(hh,f,rr)
        self.assertEqual(before['selections'][:2],after['selections'][:2])
        self.assertEqual(before['folds'][1]['matches'][0]['alternatives'],after['folds'][1]['matches'][0]['alternatives'])
    def test_identity_has_separate_clock_and_missing_sources_stay_blocked(self):
        h,f,r=native_case();f[0]['fixtures'][0]['identity_source']['available_at']=None
        out=run(h,f,r)
        self.assertEqual(out['folds'][0]['matches'][0]['status'],'blocked')
        self.assertEqual(out['predicted_n'],2)
        self.assertEqual(out['selections'][1]['selection_n'],0)
    def test_late_results_and_wrong_stage_cannot_train(self):
        h,f,r=native_case();h[-3]['verified_at']=r[0]['verified_at']='2026-10-09T09:00:00+08:00'
        out=run(h,f,r)
        self.assertEqual(out['folds'][1]['matches'][0]['alternatives']['5.0']['training_n'],35)
        self.assertEqual(out['selections'][1]['selection_n'],0)
        f[0]['fixtures'][0]['season_type']=999
        self.assertEqual(run(h,f,[])['folds'][0]['matches'][0]['status'],'blocked')
    def test_mean_gate_rejects_bias_degradation_even_if_brier_would_pass(self):
        pairs=[{'period':str(i//100),'goal_error_baseline':(-.2 if i%2 else .2),
                'goal_error_candidate':(-.1 if i%2 else .1)} for i in range(500)]
        self.assertTrue(summarize_goal_means(pairs)['mean_nondegradation_gate'])
        worse=[{**r,'goal_error_candidate':r['goal_error_candidate']+.1} for r in pairs]
        self.assertFalse(summarize_goal_means(worse)['mean_nondegradation_gate'])
        small=summarize_goal_means(pairs[:100])
        self.assertFalse(small['mean_nondegradation_gate'])
        self.assertIsNone(small['absolute_bias_delta_bootstrap_upper_95'])
        self.assertFalse(summarize_goal_means([])['mean_nondegradation_gate'])
    def test_scoring_rejects_team_id_or_halftime_conflicts(self):
        h,f,r=native_case();r[0]['provider_home_id']='999'
        with self.assertRaises(ValueError):run(h,f,r)
        h,f,r=native_case();r[0]['ht_home']=1
        with self.assertRaises(ValueError):run(h,f,r)
