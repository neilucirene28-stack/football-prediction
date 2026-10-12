"""合成研究接口验证，不创建真实赛前记录或证明生产效果。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from unittest.mock import MagicMock

import pytest

from engine import jingcai_cards_validation as cv
from engine.jingcai_cards import load_context, predict_cards
from engine.jingcai_ledger import input_digest
from scripts.import_jingcai_cards_season import import_season
from test_jingcai_review_ledger import ledger_row

ASOF=datetime.fromisoformat("2030-04-01T00:00:00+00:00")
START=datetime.fromisoformat("2030-02-01T00:00:00+00:00")


def bound_row():
    row=ledger_row();p=row['payload']['input'];r=row['payload']['result']
    raw=('Date,HomeTeam,AwayTeam,FTHG,FTAG,HY,AY,HR,AR\n'+
         ''.join(f'{d}/12/2029,合成主队,合成客队,1,1,2,3,0,1\n' for d in range(20,26))).encode()
    p['cards']={'league_code':'SYN','season':'2029/30','season_stats':import_season(
        raw,league_code='SYN',league_name=p['competition'],season='2029/30',
        start='2029-07-01',end='2030-06-30',source='synthetic',source_timezone='UTC',
        observed_at=datetime.fromisoformat(p['snapshot_at'])-timedelta(minutes=1))}
    r['input_sha256']=input_digest(p)
    r['derivatives']['cards']=predict_cards(p,datetime.fromisoformat(p['snapshot_at']))
    return row


def synthetic(i, *, version='synthetic-v1', outcome=None):
    kickoff=datetime(2030,1,1,tzinfo=timezone.utc)+timedelta(days=i)
    outcome=i%3 if outcome is None else outcome
    return {'match_id':i+1,'prediction_id':f'synthetic-{i}', 'model_version':version,
            'kickoff_at':kickoff,'snapshot_at':kickoff-timedelta(hours=1),
            'predicted_at':kickoff-timedelta(minutes=30),'settled_at':kickoff+timedelta(hours=3),
            'league':'synthetic','features':[i%4,3+i%3,.1,.2,.05,.06],
            'probs_full':[.45,.3,.25],'outcome':outcome,'score':[(2,0),(0,0),(0,2)][outcome]}


def run_synthetic(rows):
    # 缩小门槛只用于控制流测试，不改实际研究门槛。
    spec={**cv.SPEC,'min_train':6,'min_test':3,'min_folds':3,'iterations':20}
    with patch.object(cv,'SPEC',spec),patch.object(cv,'eligible_record',side_effect=lambda row,*_: (deepcopy(row),None)):
        return cv.evaluate(rows,asof=ASOF,test_start=START)


def test_bound_features_use_prematch_counts_and_never_settled_cards():
    row=bound_row();context=load_context()
    a,reason=cv.eligible_record(row,ASOF,context)
    assert reason is None and a['features'][:4]==pytest.approx([-1,5,-1,1])
    row.update(yellow_home=90,yellow_away=0,red_home=10,red_away=0)
    b,_=cv.eligible_record(row,ASOF,context)
    assert a['features']==b['features']


@pytest.mark.parametrize('kind',['hash','future','missing_current','wrong_project','wrong_source','stored_features','missing_full','coverage','low_matches'])
def test_invalid_or_unbound_features_excluded(kind):
    row=bound_row();p=row['payload']['input'];r=row['payload']['result']
    if kind=='hash':r['input_sha256']='bad'
    elif kind=='future':p['cards']['season_stats']['available_at']=p['kickoff_at'];r['input_sha256']=input_digest(p)
    elif kind=='missing_current':p['cards'].pop('season_stats');r['input_sha256']=input_digest(p)
    elif kind=='wrong_project':r['project_scope']='foreign'
    elif kind=='wrong_source':row['source']=''
    elif kind=='stored_features':r['derivatives']['cards']['red_probabilities_full']['home']=.99
    elif kind=='missing_full':r.pop('p_final_full')
    else:
        t=p['cards']['season_stats']['leagues']['SYN']['teams']['合成主队']
        if kind=='coverage':t['matches_played']=20
        else:
            # 保持总量合法但降低真实观测场数。
            for team in p['cards']['season_stats']['leagues']['SYN']['teams'].values():
                for block in ('home','away'):
                    for key in ('mp','yf','ya','rf','ra'):team[block][key]=team[block][key]//2
                team['matches_played']=3
            lg=p['cards']['season_stats']['leagues']['SYN'];lg['matches']=3
        r['input_sha256']=input_digest(p)
        r['derivatives']['cards']=predict_cards(p,datetime.fromisoformat(p['snapshot_at']))
    result,reason=cv.eligible_record(row,ASOF,load_context())
    assert result is None and reason


def test_standardization_is_training_only_and_probabilities_sum_to_one():
    rows=[synthetic(i) for i in range(6)]
    model=cv.fit(rows,use_cards=True);before=deepcopy(model)
    target=synthetic(40);target['features']=[1e10]*6
    result=cv.estimate(target,model)
    assert sum(result)==pytest.approx(1) and all(0<=p<=1 for p in result)
    assert model==before and model['means'][0]==pytest.approx(7/6)


def test_zero_offset_reproduces_baseline_and_control_has_no_card_coefficients():
    row=synthetic(1)
    assert cv.estimate(row,{'means':[],'scales':[],'weights':[[0],[0],[0]]})==pytest.approx(row['probs_full'])
    control=cv.fit([synthetic(i) for i in range(6)],use_cards=False)
    assert control['means']==[] and all(len(w)==1 for w in control['weights'])


def test_delayed_settlements_and_same_day_games_do_not_enter_training():
    rows=[synthetic(i) for i in range(36)]
    rows[0]['settled_at']=ASOF-timedelta(hours=1)
    report=run_synthetic(rows)
    folds=report['by_version']['synthetic-v1']['folds']
    assert folds[0]['train_n']==30
    assert all(datetime.fromisoformat(f['training_latest_settlement'])<datetime.fromisoformat(f['training_cutoff']) for f in folds)
    assert report['production_enabled'] is False


def test_duplicates_conflicts_and_versions_are_separate():
    rows=[synthetic(i) for i in range(36)]
    same=deepcopy(rows[0]);same['prediction_id']='duplicate'
    conflict=deepcopy(rows[1]);conflict['score']=(7,7)
    foreign_version=[synthetic(i,version='synthetic-v2') for i in range(34)]
    report=run_synthetic(rows+[same,conflict]+foreign_version)
    assert report['excluded']['additional_forecast_same_match_version']==1
    assert report['excluded']['conflicting_physical_match']==2
    assert report['by_version']['synthetic-v1']['eligible_matches']==35
    assert report['by_version']['synthetic-v2']['eligible_matches']==34
    assert all(not x['production_enabled'] for x in report['by_version'].values())


def test_same_day_batches_share_earliest_snapshot_training_cutoff():
    rows=[synthetic(i) for i in range(35)]
    extra=synthetic(70);extra['kickoff_at']=rows[31]['kickoff_at']+timedelta(hours=6)
    extra['snapshot_at']=rows[31]['snapshot_at']-timedelta(days=2)
    extra['settled_at']=extra['kickoff_at']+timedelta(hours=3)
    report=run_synthetic(rows+[extra])
    first=report['by_version']['synthetic-v1']['folds'][0]
    assert first['test_n']==2 and first['train_n']==29


def test_order_independent_scoring_and_insufficient_data_is_blocked():
    rows=[synthetic(i) for i in range(34)]
    a=run_synthetic(rows);b=run_synthetic(list(reversed(rows)))
    assert a['by_version']==b['by_version']
    small=run_synthetic([synthetic(31)])['by_version']['synthetic-v1']
    assert not small['research_screen_passed'] and small['evaluated_matches']==0


def test_negative_evidence_fails_screen_and_no_automatic_promotion():
    report=run_synthetic([synthetic(i) for i in range(36)])
    version=report['by_version']['synthetic-v1']
    assert not version['research_screen_passed'] and version['blocking_reasons']
    assert set(version['paired_comparisons'])=={'baseline','intercept_control'}


def test_no_eligible_data_and_invalid_cutoff():
    report=cv.evaluate([{},None],asof=ASOF,test_start=START)
    assert report['status']=='no_eligible_data' and report['eligible_matches']==0
    assert not report['production_enabled']
    with pytest.raises(ValueError):cv.evaluate([],asof=ASOF,test_start=ASOF)


def test_future_test_label_cannot_change_previous_fold():
    rows=[synthetic(i) for i in range(36)]
    a=run_synthetic(rows);rows[-1]['outcome']=1;rows[-1]['score']=(0,0)
    b=run_synthetic(rows)
    assert a['by_version']['synthetic-v1']['folds'][:-1]==b['by_version']['synthetic-v1']['folds'][:-1]


def test_earliest_forecast_selection_uses_latest_declared_settlement():
    rows=[synthetic(i) for i in range(35)]
    duplicate=deepcopy(rows[0]);duplicate['settled_at']=ASOF-timedelta(hours=1)
    report=run_synthetic(rows+[duplicate])
    assert report['by_version']['synthetic-v1']['folds'][0]['train_n']==30


def test_successful_screen_still_never_enables_production():
    rows=[synthetic(i) for i in range(35)]
    favorable={'n':4,'brier_delta':-.1,'logloss_delta':-.1,'upper_95_normal':-.05}
    with patch.object(cv,'_comparison',return_value=favorable):report=run_synthetic(rows)
    version=report['by_version']['synthetic-v1']
    assert version['research_screen_passed'] and not version['production_enabled']


def test_export_is_read_only_version_filtered_and_never_overwrites(tmp_path):
    from scripts.export_jingcai_cards_ledger import export_rows
    conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
    cur.fetchall.return_value=[bound_row()]
    path=tmp_path/'private-ledger.jsonl'
    report=export_rows(conn,version='synthetic-v1',days=365,asof=ASOF,output=path)
    conn.execute.assert_called_once_with('SET TRANSACTION READ ONLY')
    sql,params=cur.execute.call_args.args
    assert "= 'jingcai'" in sql and 'p.model_version = %s' in sql and 'LIMIT' not in sql
    assert params[-1]=='synthetic-v1' and report['rows']==1
    assert path.stat().st_mode & 0o777==0o600
    before=path.read_bytes()
    with pytest.raises(FileExistsError):export_rows(conn,version='synthetic-v1',days=365,asof=ASOF,output=path)
    assert path.read_bytes()==before


@pytest.mark.parametrize('version',['',None,'unknown'])
def test_export_requires_version_before_query(tmp_path,version):
    from scripts.export_jingcai_cards_ledger import export_rows
    conn=MagicMock()
    with pytest.raises(ValueError):export_rows(conn,version=version,days=365,asof=ASOF,output=tmp_path/'x')
    conn.execute.assert_not_called()
