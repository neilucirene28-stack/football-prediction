"""竞彩合成用例；不创建真实赛季或赛前封存记录。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
from unittest.mock import MagicMock

import pytest

from engine.jingcai_cards import predict_cards
from engine.jingcai_predictor import predict, PredictError
from engine.jingcai_montecarlo import simulate_matrix
from engine.jingcai_review import build_review
from api.api.jingcai_settlement import save_jingcai_settlement
from scripts.import_jingcai_cards_season import import_season
from scripts.scoreboard import score_one, aggregate

CUTOFF=datetime.fromisoformat('2030-01-10T10:00:00+08:00')
CSV=('Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HY,AY,HR,AR,Referee\n'
     'E0,08/01/2030,Arsenal,Man City,2,1,2,3,0,1,R\n'
     'E0,09/01/2030,Man City,Arsenal,1,1,4,1,0,0,R\n').encode()


def artifact(raw=CSV):
    return import_season(raw,league_code='E0',league_name='英超',season='2029/30',
        start='2029-07-01',end='2030-06-30',source='synthetic_fixture_not_live_source',
        source_timezone='Europe/London',observed_at=CUTOFF-timedelta(minutes=1))


def payload():
    return {'home':'阿森纳','away':'曼城','competition':'英超',
        'snapshot_at':CUTOFF.isoformat(),'kickoff_at':(CUTOFF+timedelta(days=1)).isoformat(),
        'league_avg_goals':2.7,'home_recent':[{'gf':2,'ga':1,'venue':'H'}]*8,
        'away_recent':[{'gf':1,'ga':1,'venue':'A'}]*8,'handicap_line':-1,
        'odds':{'home':2.1,'draw':3.5,'away':3.6},
        'cards':{'league_code':'E0','season':'2029/30','season_stats':artifact()}}


def test_current_season_counts_and_exact_identity_no_goal_adjustment():
    p=payload();before=deepcopy(p)
    r=predict(p,{'mc_min_score':101},asof=CUTOFF)
    c=r['derivatives']['cards']
    assert c['data_basis']=='current_season_with_historical_shrinkage'
    assert c['current_season_totals']['home']['yellow_observed']==3
    assert c['current_season_totals']['away']['yellow_observed']==7
    assert c['current_season_totals']['away']['red_observed']==1
    assert c['current_season_totals']['home']['matches_played']==2
    states=c['red_card_scenarios']
    assert sum(states[k] for k in ('none','home_only','away_only','both'))==pytest.approx(1)
    assert c['match_result_integration']['status']=='not_applied'
    no_cards=deepcopy(p);no_cards.pop('cards')
    base=predict(no_cards,{'mc_min_score':101},asof=CUTOFF)
    assert r['p_final_full']==base['p_final_full'] and p==before


def test_old_history_is_not_current_season_totals():
    p=payload();p['cards'].pop('season_stats')
    c=predict_cards(p,CUTOFF)
    assert c['status']=='ok' and c['data_basis']=='historical_prior_only'
    assert c['current_season_totals'] is None and c['confidence']=='low'


def test_historical_future_aggregate_is_not_used():
    p=payload();p['cards']={'league_code':'E0'}
    c=predict_cards(p,datetime.fromisoformat('2023-01-01T00:00:00+08:00'))
    assert c['status']=='insufficient_data'


def test_united_token_cannot_bind_to_man_united():
    p=payload();p['home']='Newcastle United'
    a=artifact();a['leagues']['E0']['teams']['Man United']=a['leagues']['E0']['teams'].pop('Man City')
    p['cards']['season_stats']=a
    c=predict_cards(p,CUTOFF)
    assert c['status']=='insufficient_data'


@pytest.mark.parametrize('kind',['future','season','project','source','hash','zero_matches','negative','bool','too_big','outside_season'])
def test_invalid_season_evidence_rejected(kind):
    p=payload();a=p['cards']['season_stats'];t=a['leagues']['E0']['teams']['Arsenal']['home']
    if kind=='future':a['available_at']=(CUTOFF+timedelta(seconds=1)).isoformat()
    elif kind=='season':a['season']='2028/29'
    elif kind=='project':a['project']='other'
    elif kind=='source':a['source']=''
    elif kind=='hash':a['source_sha256']='bad'
    elif kind=='zero_matches':t['mp']=0
    elif kind=='negative':t['yf']=-1
    elif kind=='bool':t['yf']=True
    elif kind=='too_big':t['yf']=10**400
    else:a['season_end']='2029-12-31'
    with pytest.raises(PredictError):predict(p,{'mc_min_score':101},asof=CUTOFF)


def test_recent_future_missing_time_wrong_team_and_duplicates_filtered():
    p=payload();base={'team':'Arsenal','league':'E0','season':'2029/30','match_id':'fixture-1',
        'completed_at':'2030-01-08T23:00:00+08:00','available_at':'2030-01-09T00:00:00+08:00','yellow':7,'red':0}
    rows=[base,dict(base),dict(base,match_id='future',available_at='2030-01-11T00:00:00+08:00'),
          dict(base,match_id='wrong-team',team='Newcastle United'),dict(base,match_id='missing-time',available_at=None),
          dict(base,match_id='old-season',season='2028/29')]
    p['cards']['recent_records']=rows
    c=predict_cards(p,CUTOFF)
    assert c['recent_form']['matches']['home']==1 and c['recent_form']['factors']['home']>1
    excluded=c['audit']['recent_excluded']
    assert excluded['late_or_invalid_time']==1 and excluded['unbound_team']==1 and excluded['duplicate_team_match']==1


def test_missing_card_counts_keep_coverage_and_are_not_zero():
    extra='E0,07/01/2030,Arsenal,Man City,1,0,,2,0,0,R\n'.encode()
    c=predict_cards(dict(payload(),cards={'league_code':'E0','season':'2029/30','season_stats':artifact(CSV+extra)}),CUTOFF)
    t=c['current_season_totals']['home']
    assert t['matches_played']==3 and t['matches_with_cards']==2
    assert t['coverage']==pytest.approx(2/3) and t['totals_complete'] is False
    assert t['yellow_observed']==3


def test_csv_duplicates_dedup_and_conflicts_fail():
    a=artifact(CSV+CSV.splitlines(keepends=True)[1])
    assert a['leagues']['E0']['matches']==2 and a['audit']['excluded']['duplicate_match']==1
    conflict='E0,08/01/2030,Arsenal,Man City,2,1,9,3,0,1,R\n'.encode()
    with pytest.raises(ValueError,match='冲突'):artifact(CSV+conflict)


def test_csv_today_future_unfinished_wrong_league_are_excluded():
    extra=('E0,10/01/2030,Arsenal,Man City,2,1,2,3,0,0,R\n'
           'E0,11/01/2030,Arsenal,Man City,2,1,2,3,0,0,R\n'
           'E0,07/01/2030,Arsenal,Man City,,,2,3,0,0,R\n'
           'E1,06/01/2030,Arsenal,Man City,2,1,2,3,0,0,R\n').encode()
    a=artifact(CSV+extra)
    assert a['leagues']['E0']['matches']==2
    assert a['audit']['excluded']['same_source_day_or_future']==2


def test_joint_ht_marginals_match_both_playtypes():
    r=predict(payload(),{'mc_min_score':101},asof=CUTOFF);d=r['derivatives'];hf=d['half_full_1x2_full']
    for i,(name,symbol) in enumerate(zip(('home','draw','away'),('胜','平','负'))):
        assert sum(v for key,v in hf.items() if key[0]==symbol)==pytest.approx(d['half_time_full'][name],abs=1e-14)
        assert sum(v for key,v in hf.items() if key[-1]==symbol)==pytest.approx(r['p_final_full'][i],abs=1e-14)
    assert d['half_time']['probability_source']=='jingcai_half_full_joint_distribution'


def test_mc_final_matrix_is_seeded_and_reference_matches_fusion():
    cfg={'mc_n':30000,'mc_seed':42,'mc_min_score':0}
    first=predict(payload(),cfg,asof=CUTOFF);second=predict(payload(),cfg,asof=CUTOFF)
    m=first['monte_carlo']
    assert m==second['monte_carlo'] and m['probability_source']=='jingcai_final_score_matrix'
    assert m['reference_probabilities']==pytest.approx(first['p_final_full'],abs=1e-12)
    for p,q,se in zip(m['p_full'],m['reference_probabilities'],m['wdl_standard_errors']):
        assert abs(p-q)<6*se+1/30000
    assert m['ran'] and m['n']==30000


def test_mc_sampler_degenerate_support_and_gate():
    matrix=[[0,0,0],[0,0,0],[1,0,0]]
    r=simulate_matrix(matrix,n=100,seed=1)
    assert r['p_full']==[1,0,0] and r['top_scores']==[{'score':'2-0','prob':1.0}]
    assert simulate_matrix(matrix,n=100,completeness=40,min_score=60)['ran'] is False


@pytest.mark.parametrize('matrix', [[[.3,.2],[.2,.2]],[[float('nan')]],[[True]],[[1,-.1],[0,.1]],[[1,0]]])
def test_mc_bad_matrix_rejected(matrix):
    with pytest.raises(ValueError):simulate_matrix(matrix,n=100)


def test_cards_scoring_full_probabilities_and_missing_denominators():
    p=payload();r=predict(p,{'mc_min_score':101},asof=CUTOFF)
    st={'home_goals':2,'away_goals':1,'yellow_home':2,'yellow_away':3,'red_home':0,'red_away':1}
    s=score_one(r,st);c=r['derivatives']['cards']
    assert s['red_any_brier']==pytest.approx((c['red_probabilities_full']['any']-1)**2)
    assert s['red_home_brier']==pytest.approx(c['red_probabilities_full']['home']**2)
    assert s['yellow_over_4_5_brier']==pytest.approx((c['yellow_over_probabilities_full']['4.5']-1)**2)
    missing=score_one(r,{'home_goals':1,'away_goals':0,'red_home':0})
    assert 'red_any_brier' not in missing and 'yellow_total_err' not in missing
    metrics=aggregate([s,missing]);assert metrics['red_any_brier_n']==1 and metrics['red_home_brier_n']==2
    assert metrics['yellow_total_err_n']==1


@pytest.mark.parametrize('bad',[-1,True,1.5])
def test_invalid_card_results_do_not_become_zero(bad):
    r=predict(payload(),{'mc_min_score':101},asof=CUTOFF)
    s=score_one(r,{'home_goals':1,'away_goals':0,'red_home':bad,'red_away':0})
    assert 'red_any_brier' not in s and 'red_home_brier' not in s


def test_jingcai_settlement_retains_null_and_checks_project_clock():
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value;cur.fetchone.return_value=('id',)
    assert save_jingcai_settlement(conn,'id',2,1,red_home=0,red_away=1,source='synthetic')
    sql,params=cur.execute.call_args.args
    assert "'jingcai'" in sql and 'clock_timestamp()' in sql and 'yellow_home' in sql
    assert params[5] is None and params[6] is None and params[7:9]==(0,1)
    conn.commit.assert_called_once()


def test_settlement_time_or_wrong_project_failure_rolls_back():
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value;cur.fetchone.side_effect=[None,None]
    with pytest.raises(ValueError,match='已开球竞彩'):save_jingcai_settlement(conn,'id',2,1,source='synthetic')
    conn.rollback.assert_called_once();conn.commit.assert_not_called()


def test_conflicting_recent_match_is_excluded_instead_of_order_selected():
    p=payload();row={'team':'Arsenal','league':'E0','season':'2029/30','match_id':'conflict',
        'completed_at':'2030-01-08T23:00:00+08:00','available_at':'2030-01-09T00:00:00+08:00','yellow':2,'red':0}
    p['cards']['recent_records']=[row,dict(row,yellow=7)]
    first=predict_cards(p,CUTOFF)
    p['cards']['recent_records'].reverse();second=predict_cards(p,CUTOFF)
    assert first==second and first['recent_form']['matches']['home']==0
    assert first['audit']['recent_excluded']['conflicting_team_match']==2


@pytest.mark.parametrize('key,value',[('leagues',[]),('teams',[]),('referees',[])])
def test_malformed_artifact_structures_raise_predict_error(key,value):
    p=payload();a=p['cards']['season_stats']
    if key=='leagues':a[key]=value
    else:a['leagues']['E0'][key]=value
    with pytest.raises(PredictError):predict(p,{'mc_min_score':101},asof=CUTOFF)


def test_malformed_optional_card_prediction_does_not_break_goal_scoring():
    p={'p_home':.6,'p_draw':.25,'p_away':.15,'derivatives':{'cards':[1,2]}}
    s=score_one(p,{'home_goals':1,'away_goals':0})
    assert 'brier' in s and 'cards:invalid_prediction' in s['unscored_playtypes']


def test_invalid_settlement_card_count_fails_before_database():
    conn=MagicMock()
    with pytest.raises(ValueError):save_jingcai_settlement(conn,'id',1,0,red_home=True,source='synthetic')
    conn.cursor.assert_not_called()


def test_domestic_card_counts_not_used_for_cup_fixture():
    p=payload();p['competition']='英联杯'
    assert predict_cards(p,CUTOFF)['status']=='insufficient_data'


def test_ledger_review_receives_real_card_columns_and_counts_them():
    from test_jingcai_review_ledger import ledger_row, ASOF
    row=ledger_row();r=row['payload']['result']
    r['derivatives']['cards']={'status':'ok','p_red':.1,'p_home_red':.04,'p_away_red':.06,
        'p_over_3_5':.7,'p_over_4_5':.5,'exp_total_yellow':4.5}
    row.update(yellow_home=2,yellow_away=3,red_home=0,red_away=1)
    report=build_review([row],asof=ASOF)
    metrics=report['by_version']['synthetic-v1']['metrics']
    assert metrics['red_any_brier_n']==1 and metrics['red_any_brier']==pytest.approx(.81)
    assert metrics['yellow_total_err']==pytest.approx(.5)


def test_batch_entry_passes_season_snapshot_and_outputs_cards(monkeypatch,tmp_path):
    import json
    from test_jingcai_review_ledger import live_payload
    from scripts import batch_predict_today as batch
    p=live_payload();p['competition']='英超';now=datetime.now(timezone.utc)
    day=(now-timedelta(days=2)).strftime('%d/%m/%Y')
    raw=(f'Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HY,AY,HR,AR\n'
         f'E0,{day},{p["home"]},{p["away"]},2,1,2,3,0,1\n').encode()
    a=import_season(raw,league_code='E0',league_name='英超',season='synthetic-batch-season',
        start=f'{now.year}-01-01',end=f'{now.year}-12-31',source='synthetic_fixture_not_live_source',
        source_timezone='UTC',observed_at=now-timedelta(hours=1))
    spec={'league_code':'E0','season':'synthetic-batch-season','season_stats':a}
    fixture={'matchid':'fixture','rq':-1,'cards':spec}
    class Source:
        def __init__(self,**kwargs):pass
        def _get(self,url):return 'synthetic'
        def parse_detail(self,html,mid):return {}
        def _build_match(self,info):
            return dict(p,home_team=p['home'],away_team=p['away'],external_id='titan-fixture')
    monkeypatch.setattr(batch,'Titan007Source',Source)
    monkeypatch.setattr(batch,'_db_conn',lambda:None)
    monkeypatch.setattr(batch.time,'sleep',lambda _:None)
    monkeypatch.setattr(batch,'_CALIBRATION_CONFIG',None)
    inp=tmp_path/'fixtures.json';out=tmp_path/'predictions.json'
    inp.write_text(json.dumps([fixture],ensure_ascii=False))
    batch.main(str(inp),str(out))
    result=json.loads(out.read_text())[0]
    assert result['cards']==result['prediction']['derivatives']['cards']
    assert result['cards']['current_season_totals']['home']['yellow_observed']==2
    assert result['input']['cards']==spec
    assert result['prediction']['project_scope']=='jingcai'


def test_competition_or_future_suspensions_not_inferred_from_team_counts():
    p=payload();base={'team':'Arsenal','player_id':'synthetic-player','player_name':'合成球员',
        'status':'confirmed','competition':'英超','kickoff_at':p['kickoff_at'],
        'available_at':'2030-01-09T10:00:00+08:00','source':'synthetic_fixture'}
    p['cards']['confirmed_suspensions']=[base,dict(base,competition='英联杯'),
        dict(base,available_at='2030-01-11T00:00:00+08:00'),dict(base,status='possible')]
    c=predict_cards(p,CUTOFF);s=c['confirmed_suspensions']
    assert s['records']==[base] and len(s['excluded'])==3
    assert 'not_applied' in s['match_result_effect']


@pytest.mark.parametrize('key,value',[('matches',99),('home_yellow',99)])
def test_inconsistent_season_aggregate_rejected(key,value):
    p=payload();p['cards']['season_stats']['leagues']['E0'][key]=value
    with pytest.raises(PredictError,match='不一致'):predict(p,{'mc_min_score':101},asof=CUTOFF)
