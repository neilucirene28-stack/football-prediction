"""合成契约用于解释逻辑验证，不用于训练或宣称预测改善。"""
from copy import deepcopy
import pytest
from engine.jingcai_reporting import lineup_confirmation, score_explanation
from engine.jingcai_core import consistency_check
from engine.jingcai_predictor import predict
from scripts.jingcai_format import format_top_scores
from test_jingcai_inputs_grid import payload, CONFIG, ASOF
from test_jingcai_team_intelligence import bound

@pytest.mark.parametrize('home,away,confirmed', [
    ('confirmed','confirmed',True), ('expected','confirmed',False),
    ('unknown','confirmed',False), ('confirmed','unknown',False)])
def test_lineup_requires_both_validated_confirmations(home,away,confirmed):
    audit={'team_observations': {'selected_by_dimension': {
        'home': {'lineup': {'lineup_status': home, 'source': 'espn'}},
        'away': {'lineup': {'lineup_status': away, 'source': 'espn'}}}}}
    assert lineup_confirmation(audit)['both_confirmed'] is confirmed


def test_conflicting_lineup_status_does_not_upgrade():
    audit={'team_observations': {'selected_by_dimension': {'home': {
        'lineup': {'lineup_status': 'expected', 'source': 'sporttery'}}}},
        'team_intelligence': {'teams': {'home': {'lineup_status': 'confirmed'},
                                       'away': {'lineup_status': 'confirmed'}}}}
    r=lineup_confirmation(audit)
    assert not r['both_confirmed'] and r['sides']['home']['status_conflict']


def test_nonempty_injury_is_not_lineup_confirmation():
    p=payload();p['injury']={'home': 0, 'away': 0}
    r=predict(p,CONFIG,asof=ASOF)
    assert r['confidence_audit']['lineup_factor']==0.6
    assert not r['confidence_audit']['lineup']['both_confirmed']


def test_confirmed_lineup_changes_quality_only():
    base=predict(payload(),CONFIG,asof=ASOF)
    r=predict(bound(),CONFIG,asof=ASOF)
    assert r['confidence_audit']['lineup_factor']==1
    assert r['confidence_score'] > base['confidence_score']
    assert r['p_final_full']==base['p_final_full']
    assert r['score_matrix_full']==base['score_matrix_full']


def test_expected_lineup_does_not_gain_confirmation_credit():
    p=bound()
    for t in p['team_intelligence']['teams'].values(): t['lineup']['status']='expected'
    r=predict(p,CONFIG,asof=ASOF)
    assert r['confidence_audit']['lineup_factor']==0.6


def test_aggregated_winner_and_modal_score_can_coexist():
    matrix=[[0.05,0.1,0.0],[0.20,0.3,0.0],[0.15,0.20,0.0]]
    before=deepcopy(matrix)
    e=score_explanation(matrix,[0.55,0.35,0.10])
    assert e['global_mode']['score']=='1-1'
    assert e['global_mode_direction_differs']
    assert e['scores_within_wdl_direction'][0]['prob']==0.2
    assert e['scores_within_wdl_direction'][0]['score']=='1-0'
    assert matrix==before
    scores=[{'score':'1-1','prob':0.3}]
    assert consistency_check([.55,.35,.10],scores,None,None)
    assert not consistency_check([.55,.35,.10],scores,None,None,check_modal_score=False)
    text=format_top_scores({'derivatives': {'top_scores':scores,'score_explanation':e}})
    assert '主胜' in text and '1-0(20.0%)' in text and '可同时成立' in text


def test_high_total_probability_does_not_require_high_modal_score():
    assert not consistency_check([.4,.3,.3],[{'score':'1-0','prob':.12}],None,
                                 {'over':.7},check_modal_score=False)


def test_quarantined_source_conflict_does_not_gain_confirmation_credit():
    audit={'team_observations': {'conflicts': [{'side':'home'}]},
           'team_intelligence': {'teams': {s:{'lineup_status':'confirmed'} for s in ('home','away')}}}
    assert not lineup_confirmation(audit)['both_confirmed']
