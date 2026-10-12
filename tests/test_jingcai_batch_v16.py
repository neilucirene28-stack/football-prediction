"""批量导出不能重新生成被核心拒绝的先验预测。"""
from copy import deepcopy
from unittest.mock import patch
import pytest
from engine.jingcai_batch import predict_batch
from engine.jingcai_predictor import PredictError
from test_jingcai_evidence_v16 import payload


def test_batch_keeps_no_data_and_invalid_rows_separate_from_predictions():
    records=[{'number':'001','payload':payload()},
             {'number':'002','payload':payload(home_recent=[],away_recent=[],odds=None,asian=None,ou_line=None)},
             {'number':'003','payload':payload(odds={'home':0,'draw':3.,'away':4.})}]
    before=deepcopy(records);result=predict_batch(records,{'mc_min_score':101})
    assert (result['requested_n'],result['predicted_n'],result['abstained_n'])==(3,1,2)
    assert [r['number'] for r in result['records']]==['001'] and records==before
    assert [r['status'] for r in result['abstained']]==['insufficient_data','invalid_input']
    assert all('prediction' not in r and 'p_home' not in r for r in result['abstained'])
    assert result['wdl_classification']['forecast_n']==1
    assert result['wdl_classification']['scored_n']==0


@pytest.mark.parametrize('kind',['duplicate','missing_number','mixed_project'])
def test_invalid_batch_shape_is_rejected_before_any_forecast(kind):
    rows=[{'number':'001','payload':payload()},{'number':'002','payload':payload()}]
    if kind=='duplicate':rows[1]['number']='001'
    elif kind=='missing_number':rows[1].pop('number')
    else:rows[1]['payload']['project']='beidan'
    with patch('engine.jingcai_predictor.predict') as called:
        with pytest.raises(PredictError):predict_batch(rows,{'mc_min_score':101})
    called.assert_not_called()


def test_zero_data_batch_does_not_report_dummy_draw_or_home_probabilities():
    r=predict_batch([{'number':'001','payload':payload(home_recent=[],away_recent=[],odds=None,asian=None,ou_line=None)}])
    assert r['records']==[] and r['predicted_n']==0 and r['abstained_n']==1
    assert r['wdl_classification']['forecast_top1_counts']==dict(home=0,draw=0,away=0)


def test_batch_historical_replay_remains_unsealed():
    from test_jingcai_inputs_grid import payload as historical_payload,ASOF
    r=predict_batch([{'number':'001','payload':historical_payload()}],{'mc_min_score':101},asof=ASOF)
    assert r['evaluation_mode']==r['records'][0]['prediction']['evaluation_mode']=='historical_replay'
    assert r['persistence']['status']=='not_saved'
