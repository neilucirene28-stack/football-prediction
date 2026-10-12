"""无数据兜底与市场预测隔离；全部为合成输入，未制造历史预测。"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from unittest.mock import patch

import pytest

from engine.jingcai_predictor import predict
from engine.jingcai_evidence import signal_evidence
from engine.jingcai_ledger import input_digest,validate_live_record
from engine.poisson import match_probs,handicap_1x2
from engine.market import shin_probs
from api.api.persist import build_prediction_row


def payload(**overrides):
    now=datetime.now(timezone.utc)
    p=dict(home='合成主队',away='合成客队',competition='合成联赛',
           kickoff_at=(now+timedelta(days=2)).isoformat(),snapshot_at=(now-timedelta(seconds=1)).isoformat(),
           home_recent=[dict(gf=2,ga=1,venue='H')]*8,
           away_recent=[dict(gf=1,ga=1,venue='A')]*8,
           odds=dict(home=3.7,draw=2.25,away=3.8),asian=dict(handicap=0.),ou_line=2.5,
           handicap_line=-1)
    p.update(overrides);return p


def test_empty_inputs_have_no_prior_probabilities_or_recommendations():
    p=payload(home_recent=[],away_recent=[],odds=None,asian=None,ou_line=None)
    r=predict(p,dict(mc_min_score=101))
    assert r['status']=='insufficient_data' and r['completeness']==0
    assert r['forecast_evidence']['status']=='prior_only_blocked'
    assert r['model']=='jingcai' and r['model_version'].startswith('v2.14+')
    assert not {'p_home','p_draw','p_away','recommendation','derivatives'} & r.keys()


def test_insufficient_data_api_does_not_open_database():
    from api.api.routes import predict as route
    p=payload(home_recent=[],away_recent=[],odds=None,asian=None,ou_line=None)
    with patch.object(route,'get_conn') as connection:
        r=route.run_predict(p)
    connection.assert_not_called()
    assert r['persistence']['reason']=='prediction_not_ok'


def test_wrong_venue_histories_cannot_manufacture_a_form_signal():
    p=payload(odds=None,home_recent=[dict(gf=2,ga=1,venue='A')]*8,
              away_recent=[dict(gf=1,ga=1,venue='H')]*8)
    r=predict(p,dict(mc_min_score=101))
    assert r['completeness']>=40 and r['status']=='insufficient_data'
    assert r['forecast_evidence']['history']['home']['usable']==0
    assert 'recommendation' not in r


@pytest.mark.parametrize('histories', ['empty','wrong_venue','only_one_team'])
def test_market_only_never_blends_a_generic_team_prior(histories):
    p=payload()
    if histories=='empty':p.update(home_recent=[],away_recent=[])
    elif histories=='wrong_venue':
        p['home_recent']=[dict(gf=2,ga=1,venue='A')]*8
        p['away_recent']=[dict(gf=1,ga=1,venue='H')]*8
    else:p['away_recent']=[]
    before=deepcopy(p);r=predict(p,dict(mc_min_score=101))
    expected=shin_probs(tuple(p['odds'][k] for k in ('home','draw','away')))
    assert r['status']=='ok' and r['forecast_evidence']['mode']=='market_only'
    assert r['weights']=={'model':0.,'market':1.,'elo':0.}
    assert r['signals_full']['model'] is None and r['derivatives']['p_1x2_raw'] is None
    assert r['p_final_full']==pytest.approx(expected,abs=1e-14)
    assert r['recommendation']['direction']=='平局' and p==before
    assert any('比分总进球' in line for line in r['notes'])
    assert match_probs(r['score_matrix_full'])==pytest.approx(expected,abs=1e-12)
    assert handicap_1x2(r['score_matrix_full'],-1)==pytest.approx(
        [r['derivatives']['handicap_1x2'][f'p_{k}_full'] for k in ('home','draw','away')],abs=1e-12)
    assert build_prediction_row(1,p,r)['model_version']==r['model_version']


def test_explicit_zero_market_weight_does_not_fall_back_to_prior():
    p=payload(home_recent=[],away_recent=[])
    r=predict(p,dict(mc_min_score=101,market_weight=0.))
    assert r['status']=='insufficient_data' and '正权重' in r['reason']


@pytest.mark.parametrize('claimed_grade,claimed_score', [('D',0),('A',0),('A',70)])
def test_forged_ok_fallback_is_rejected_by_persistence(claimed_grade,claimed_score):
    original=payload();r=predict(original,dict(mc_min_score=101))
    p={k:v for k,v in original.items() if k not in ('home_recent','away_recent','odds','asian','ou_line')}
    r.update(grade=claimed_grade,completeness=claimed_score,input_sha256=input_digest(p))
    r.pop('forecast_evidence',None)
    with pytest.raises(ValueError,match='无数据|无有效比赛数据'):
        build_prediction_row(1,p,r)


def test_prior_with_fake_model_weight_cannot_enter_market_only_ledger():
    p=payload(home_recent=[],away_recent=[]);r=predict(p,dict(mc_min_score=101))
    r['weights'].update(model=.4,market=.6)
    with pytest.raises(ValueError,match='先验模型正权重'):validate_live_record(p,r)


def test_evidence_rebinding_detects_fake_form_status():
    p=payload(home_recent=[],away_recent=[]);r=predict(p,dict(mc_min_score=101))
    r['forecast_evidence']['form_available']=True
    with pytest.raises(ValueError,match='证据与原输入不一致'):validate_live_record(p,r)


def test_home_away_alias_and_unknown_venue_follow_the_estimator_rules():
    p=payload(home_recent=[dict(gf=1,ga=1,home_away='H')]*8,
              away_recent=[dict(gf=1,ga=1)]*8)
    e=signal_evidence(p);r=predict(p,dict(mc_min_score=101))
    assert e['form_available'] and e==r['forecast_evidence']
    validate_live_record(p,r)


@pytest.mark.parametrize('bad', [True,-1,float('nan')])
def test_invalid_goals_do_not_provide_history_evidence(bad):
    p=payload();p['home_recent']=[dict(gf=bad,ga=1,venue='H')]*8
    assert signal_evidence(p)['form_available'] is False


def test_legacy_review_never_learns_explicit_zero_completeness_prior():
    from test_jingcai_review_ledger import ledger_row,ASOF
    from engine.jingcai_review import build_review
    ordinary=ledger_row();prior=deepcopy(ordinary);prior['match_id']=2
    prior['payload']['result'].update(grade='D',completeness=0)
    review=build_review([ordinary,prior],asof=ASOF)
    assert review['n']==1 and sum(review['excluded'].values())==1


def test_unknown_legacy_input_evidence_is_described_but_cannot_train_weights():
    from test_jingcai_review_ledger import ledger_row,ASOF
    from engine.jingcai_review import build_review
    row=ledger_row();p=row['payload']['input'];r=row['payload']['result']
    for key in ('home_recent','away_recent','odds'):p.pop(key)
    for key in ('grade','completeness'):r.pop(key)
    r['input_sha256']=input_digest(p)
    review=build_review([row],asof=ASOF)['by_version']['synthetic-v1']
    assert review['metrics']['n']==1 and review['not_eligible_for_shadow_learning']==1
    assert review['shadow_by_signal_cohort']=={}


def test_no_data_report_never_fabricates_zero_probability_playtypes():
    from scripts.jingcai_format import format_five_playtypes,format_1x2
    r=predict(payload(home_recent=[],away_recent=[],odds=None,asian=None,ou_line=None),dict(mc_min_score=101))
    assert '暂停推荐' in format_1x2(r)
    lines=format_five_playtypes(r)
    assert len(lines)==1 and '暂停推荐' in lines[0] and '0.000' not in lines[0]


def test_report_identifies_market_only_provenance():
    from scripts.jingcai_format import format_1x2
    r=predict(payload(home_recent=[],away_recent=[]),dict(mc_min_score=101))
    assert '依据为市场赔率，球队战绩不足' in format_1x2(r)
