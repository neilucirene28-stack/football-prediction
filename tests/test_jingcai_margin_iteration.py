"""合成用例检验契约和无泄漏控制；不作为真实准确率证据。"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from engine.jingcai_inputs import validate_payload
from engine.jingcai_handicap import handicap_outcome
from engine.jingcai_ledger import input_digest
from engine.jingcai_predictor import predict, PredictError
from engine import jingcai_margin_validation as mv
from test_jingcai_review_ledger import ledger_row
from test_jingcai_inputs_grid import payload, ASOF, CONFIG


def test_object_odds_and_sequence_are_equivalent_without_mutation():
    original=payload(handicap_sp={'away':1.48,'home':5,'draw':3.95})
    before=deepcopy(original)
    normalized,audit=validate_payload(original,ASOF)
    assert normalized['handicap_sp']==[5,3.95,1.48] and original==before
    assert audit['optional_signal_status']['handicap_sp']['status']=='accepted'
    assert 'handicap_sp' in audit['features_without_available_at']
    a=predict(original,CONFIG,asof=ASOF)
    b=predict(payload(handicap_sp=[5,3.95,1.48]),CONFIG,asof=ASOF)
    assert a['p_final_full']==b['p_final_full']
    assert a['handicap_market_diagnostic']['probabilities_full']==b['handicap_market_diagnostic']['probabilities_full']


def test_source_and_availability_survive_normalization():
    _,a=validate_payload(payload(handicap_sp={'home':5,'draw':3.95,'away':1.48,
                        'source':'synthetic','available_at':ASOF.isoformat()}),ASOF)
    s=a['optional_signal_status']['handicap_sp']
    assert s['source']=='synthetic' and s['available_at']==ASOF.isoformat()
    assert 'handicap_sp' not in a['features_without_available_at']


@pytest.mark.parametrize('field',['available_at','collected_at','feature_as_of'])
def test_future_odds_metadata_cannot_be_silently_excluded(field):
    with pytest.raises(PredictError,match='未来泄漏'):
        predict(payload(handicap_sp={'home':5,'draw':3.95,'away':1.48,
                   field:(ASOF+timedelta(days=1)).isoformat()}),CONFIG,asof=ASOF)


@pytest.mark.parametrize('value',[{'home':5,'draw':3.95}, {'home':5,'draw':3.95,'away':True},
    {'home':5,'draw':3.95,'away':float('nan')}, {'home':5,'draw':3.95,'away':1},
    {'home':5,'draw':3.95,'away':1.48,'visitor':2}])
def test_bad_optional_odds_are_explicitly_excluded(value):
    out=predict(payload(handicap_sp=value),CONFIG,asof=ASOF)
    assert out['input_audit']['optional_signal_status']['handicap_sp']['status']=='excluded'
    assert any('让球赔率未使用' in n for n in out['notes'])


@pytest.mark.parametrize('hg,ag,rq,expected',[(0,0,-1,2),(2,1,-1,1),(0,1,1,1),(3,0,-3,1)])
def test_integer_handicap_settlement(hg,ag,rq,expected):
    assert handicap_outcome(hg,ag,rq)==expected


@pytest.mark.parametrize('hg,ag,rq',[(None,0,-1),(True,0,-1),(1.2,0,-1),(1,0,-.5)])
def test_unknown_and_non_integer_scores_do_not_become_zero(hg,ag,rq):
    with pytest.raises(ValueError): handicap_outcome(hg,ag,rq)


def margin_row():
    r=ledger_row(); p=r['payload']['input'];q=r['payload']['result']
    p['handicap_line']=-1; q['input_sha256']=input_digest(p)
    matrix=[[.1,.1,0],[.1,.4,.1],[.1,.05,.05]]
    q['score_matrix_full']=matrix
    probs=mv.matrix_probabilities(matrix,0)
    q['p_final_full']=probs
    for name,v in zip(('home','draw','away'),probs):
        r['p_'+name]=v;q['p_'+name]=v
    hp=mv.matrix_probabilities(matrix,-1)
    q['derivatives']['handicap_1x2']={'line':-1, **{f'p_{k}_full':v for k,v in zip(('home','draw','away'),hp)}}
    return r


def test_bound_matrix_accepted_and_inconsistent_line_or_matrix_rejected():
    r=margin_row(); assert mv.eligible_record(r,datetime.fromisoformat("2030-01-10T12:00:00+08:00"))[1] is None
    bad=deepcopy(r);bad['payload']['result']['derivatives']['handicap_1x2']['line']=1
    assert mv.eligible_record(bad,datetime.fromisoformat("2030-01-10T12:00:00+08:00"))[1]=='mismatched_handicap_line'
    bad=deepcopy(r);bad['payload']['result']['score_matrix_full'][0][0]+=.1
    assert mv.eligible_record(bad,datetime.fromisoformat("2030-01-10T12:00:00+08:00"))[0] is None
    bad=deepcopy(r);bad['payload']['result']['input_sha256']='fake'
    assert mv.eligible_record(bad,datetime.fromisoformat("2030-01-10T12:00:00+08:00"))[0] is None


@pytest.mark.parametrize('rq',[-1,1,-2,2,0])
def test_tilt_preserves_wdl_and_total_mass(rq):
    matrix=margin_row()['payload']['result']['score_matrix_full']
    q=mv.tilt(matrix,rq,1)
    assert sum(map(sum,q))==pytest.approx(1)
    assert mv.matrix_probabilities(q,0)==pytest.approx(mv.matrix_probabilities(matrix,0))
    if rq==0: assert q==matrix
    else: assert mv.matrix_probabilities(q,rq)[1]>=mv.matrix_probabilities(matrix,rq)[1]-1e-12


def synthetic(i,version='synthetic-v1'):
    kickoff=datetime(2030,1,1,tzinfo=timezone.utc)+timedelta(days=i)
    return {'match_id':i+1,'prediction_id':str(i),'model_version':version,'league':'synthetic',
            'kickoff_at':kickoff,'snapshot_at':kickoff-timedelta(hours=1),
            'predicted_at':kickoff-timedelta(minutes=30),'settled_at':kickoff+timedelta(hours=3),
            'matrix':margin_row()['payload']['result']['score_matrix_full'], 'rq':-1,'score':(1,0)}


def test_walkforward_uses_available_results_and_never_writes_production():
    rows=[synthetic(i) for i in range(10)]
    rows[0]['settled_at']=datetime(2030,4,1,tzinfo=timezone.utc)
    with patch.object(mv,'eligible_record',side_effect=lambda row,*_:(deepcopy(row),None)), \
         patch.object(mv,'SPEC',{**mv.SPEC,'min_train_per_line':3,'min_test':3}):
        out=mv.evaluate(rows,asof='2030-05-01T00:00:00+00:00',test_start='2030-01-05T00:00:00+00:00')
    folds=out['by_version']['synthetic-v1']['folds']
    assert folds[0]['train_n']==3
    assert all(datetime.fromisoformat(f['training_latest_settlement'])<datetime.fromisoformat(f['training_cutoff']) for f in folds)
    assert out['production_enabled'] is False


def test_no_training_data_is_not_evidence_of_success():
    r=margin_row()
    out=mv.evaluate([r],asof="2030-01-10T12:00:00+08:00",test_start='2030-01-01T00:00:00+08:00')
    v=out['by_version']['synthetic-v1']
    assert v['candidate']['n']==0 and not v['research_screen_passed']
    assert 'insufficient_test_matches' in v['blocking_reasons']


def test_versions_duplicates_and_conflicts_are_not_pooled():
    rows=[synthetic(i) for i in range(8)]
    duplicate=deepcopy(rows[0]); conflict=deepcopy(rows[1]);conflict['rq']=1
    rows+= [duplicate,conflict,synthetic(2,'synthetic-v2')]
    with patch.object(mv,'eligible_record',side_effect=lambda row,*_:(deepcopy(row),None)):
        out=mv.evaluate(rows,asof='2030-05-01T00:00:00+00:00',test_start='2030-01-05T00:00:00+00:00')
    assert out['excluded']['additional_forecast_same_match_version']==1
    assert out['excluded']['conflicting_physical_match']==2
    assert out['by_version']['synthetic-v1']['eligible_matches']==7
    assert out['by_version']['synthetic-v2']['eligible_matches']==1
