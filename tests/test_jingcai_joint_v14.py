"""v14数学、来源门控和入口回归；合成数据不是命中率证据。"""
from copy import deepcopy

import pytest

from engine.jingcai_joint import (conditioned_score_matrix, fuse_handicap_fixed_wdl,
                                goal_mean_from_market, market_weights)
from engine.jingcai_predictor import predict, PredictError
from engine.poisson import score_matrix, match_probs, handicap_1x2, ipf_to_marginals
from test_jingcai_inputs_grid import payload, ASOF, CONFIG


@pytest.mark.parametrize('line', [-3,-2,-1,0,1,2,3])
@pytest.mark.parametrize('weight', [0.,.5,1.])
def test_handicap_projection_keeps_wdl_and_probability_mass(line, weight):
    m = score_matrix(1.8, 1.1, rho=-.13); before = deepcopy(m)
    q, notes = fuse_handicap_fixed_wdl(m, line, [.12,.7,.18], weight)
    assert m == before
    assert sum(map(sum,q)) == pytest.approx(1., abs=1e-12)
    assert match_probs(q) == pytest.approx(match_probs(m), abs=1e-12)
    assert all(v >= 0 for row in q for v in row)
    if line == -1:
        assert handicap_1x2(q,line)[2] == pytest.approx(sum(match_probs(m)[1:]),abs=1e-12)
    if line == 1:
        assert handicap_1x2(q,line)[0] == pytest.approx(sum(match_probs(m)[:2]),abs=1e-12)
    if line == 0 or weight == 0:
        assert q == m


def test_feasible_handicap_target_recovered_at_full_weight():
    m = score_matrix(2.,1.)
    fixed = sum(match_probs(m)[1:])
    target = [(1-fixed)*.3,(1-fixed)*.7,fixed]
    q,notes = fuse_handicap_fixed_wdl(m,-1,target,1.)
    assert handicap_1x2(q,-1) == pytest.approx(target,abs=1e-12)
    assert not notes['target_projected_for_coherence']


def test_unreachable_handicap_probability_is_not_forced():
    m = score_matrix(.8,2.)
    q,notes = fuse_handicap_fixed_wdl(m,-1,[.01,.98,.01],1.)
    assert handicap_1x2(q,-1)[1] <= match_probs(m)[0]+1e-12
    assert notes['target_projected_for_coherence']


@pytest.mark.parametrize('target', [[.7,.2,.1],[.1,.25,.65],[.34,.32,.34]])
def test_conditioned_rates_change_shape_then_recover_wdl(target):
    m,note = conditioned_score_matrix(1.4,1.3,target)
    assert sum(note['effective_score_lambdas']) == pytest.approx(2.7, abs=1e-12)
    final = ipf_to_marginals(m,target)
    assert match_probs(final) == pytest.approx(target,abs=1e-12)
    assert note['poisson_tail_mass'] <= 1e-6
    ph,pd,pa = match_probs(m)
    assert ph/(ph+pa) == pytest.approx(target[0]/(target[0]+target[2]),abs=1e-7)


def test_goal_market_root_matches_poisson_probability():
    from math import exp
    mu = goal_mean_from_market(1.7,2.2)
    assert exp(-mu)*(1+mu+mu*mu/2) == pytest.approx((1/2.2)/(1/1.7+1/2.2))
    with pytest.raises(ValueError):goal_mean_from_market(1.7,2.2,line=2.)


@pytest.mark.parametrize('change',[{'market_weight':1.1},{'market_weight':True},
    {'score_shape_mode':'bad'},{'score_total_market_weight':-.1},{'handicap_market_weight':1.1}])
def test_invalid_candidate_parameters_rejected(change):
    with pytest.raises(PredictError):predict(payload(),dict(CONFIG,**change),asof=ASOF)


def test_full_candidate_same_matrix_all_three_priority_markets():
    p = payload()
    p['handicap_sp'] = dict(home=3.8,draw=3.2,away=1.7,line=-1,
        source='test',available_at=p['snapshot_at'])
    p['goals_market'] = dict(over=1.7,under=2.2,line=2.5,
        source='test',available_at=p['snapshot_at'])
    before = deepcopy(p)
    cfg = dict(CONFIG,market_weight=.75,score_shape_mode='market_conditioned',
               score_total_market_weight=.5,handicap_market_weight=.5)
    r = predict(p,cfg,asof=ASOF);m=r['score_matrix_full']
    assert p == before and r['evaluation_mode']=='historical_replay'
    assert match_probs(m) == pytest.approx(r['p_final_full'],abs=1e-12)
    assert handicap_1x2(m,-1) == pytest.approx([r['derivatives']['handicap_1x2']['p_'+k+'_full']
        for k in ('home','draw','away')],abs=1e-12)
    assert r['handicap_market_diagnostic']['probability_fusion']
    assert r['score_shape_audit']['total_market_weight_applied']==.5
    assert r['lambda_notes']['home_sample'] > 0
    ranked=sorted(((f'{h}-{a}',v) for h,row in enumerate(m) for a,v in enumerate(row)),key=lambda x:-x[1])
    assert r['derivatives']['top_scores'][0]['score']==ranked[0][0]


def test_handicap_only_entry_does_not_require_wdl_odds():
    p=payload(odds=None,asian=None,ou_line=None)
    p['handicap_sp']=dict(home=2.5,draw=3.3,away=2.5,line=-1,
        source='test',available_at=p['snapshot_at'])
    r=predict(p,dict(CONFIG,handicap_market_weight=.5),asof=ASOF)
    assert r['status']=='ok' and r['handicap_fusion_audit']['status']=='applied'
    assert r['signals_full']['market'] is None


def test_missing_handicap_source_or_time_keeps_diagnostic_only():
    p=payload(handicap_sp=[2.5,3.3,2.5])
    r=predict(p,dict(CONFIG,handicap_market_weight=.5),asof=ASOF)
    assert r['handicap_fusion_audit']['status']=='excluded'
    assert not r['handicap_market_diagnostic']['probability_fusion']


def test_future_optional_market_is_not_swallowed():
    for name,block in [('goals_market',dict(over=1.7,under=2.2,line=2.5)),
                       ('handicap_sp',dict(home=2.5,draw=3.3,away=2.5))]:
        block.update(source='test',available_at='2030-01-02T10:00:00+08:00')
        with pytest.raises(PredictError,match='未来泄漏'):
            predict(payload(**{name:block}),CONFIG,asof=ASOF)


def test_handicap_wrong_line_excluded():
    r=predict(payload(handicap_sp=dict(home=2.5,draw=3.3,away=2.5,line=1)),
        dict(CONFIG,handicap_market_weight=.5),asof=ASOF)
    assert r['input_audit']['optional_signal_status']['handicap_sp']['status']=='excluded'


def test_missing_total_market_evidence_does_not_change_requested_mean():
    p=payload(goals_market=dict(over=1.7,under=2.2,line=2.5))
    r=predict(p,dict(CONFIG,score_shape_mode='market_conditioned',score_total_market_weight=1.),asof=ASOF)
    assert r['input_audit']['goals_market']['status']=='excluded'
    assert r['score_shape_audit']['total_market_weight_applied']==0


def test_no_wdl_market_keeps_form_shape_and_legacy_weights():
    r=predict(payload(odds=None),dict(CONFIG,market_weight=.75,score_shape_mode='market_conditioned'),asof=ASOF)
    assert r['score_shape_audit']['status']=='legacy'
    assert r['weights']['model']==1.
    assert market_weights({'model':.4,'market':.4,'elo':.2},.75,True)==pytest.approx(
        {'model':1/6,'market':.75,'elo':1/12})


def test_report_global_top2_are_exact_two_largest_cells():
    from scripts.jingcai_format import format_top_scores
    r=predict(payload(),CONFIG,asof=ASOF)
    e=r['derivatives']['score_explanation'];m=r['score_matrix_full']
    ranked=sorted(((h,a,v) for h,row in enumerate(m) for a,v in enumerate(row)),key=lambda x:(-x[2],x[0],x[1]))
    assert e['global_top2']==[{'score':f'{h}-{a}','prob':v} for h,a,v in ranked[:2]]
    assert e['global_top2_coverage']==pytest.approx(sum(v for h,a,v in ranked[:2]))
    assert '比分Top2' in format_top_scores(r) and '另计' in format_top_scores(r)
