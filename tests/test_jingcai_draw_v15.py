"""平局类别验收、真实双选概率和接近平局首选的全精度回归。"""
from copy import deepcopy
from unittest.mock import patch
import pytest

from engine.jingcai_draw import class_audit,draw_view
from engine.jingcai_predictor import predict
from scripts.jingcai_format import format_1x2
from test_jingcai_inputs_grid import payload,CONFIG,ASOF


def test_zero_draw_predictions_with_actual_draws_are_visible():
    rows=[{'probs_full':[.4,.3,.3],'outcome':1} for _ in range(5)]
    before=deepcopy(rows);r=class_audit(rows)
    assert r['forecast_top1_counts']['draw']==0
    assert r['expected_draws_all_forecasts']==pytest.approx(1.5)
    assert r['by_class']['draw']['actual']==5
    assert r['by_class']['draw']['recall']==0.
    assert r['by_class']['draw']['precision'] is None
    assert r['zero_draw_top1_attention'] and rows==before


def test_unknown_outcome_is_not_a_draw_or_a_loss():
    r=class_audit([{'probs_full':[.4,.3,.3],'outcome':None}])
    assert r['forecast_n']==1 and r['scored_n']==0 and r['missing_outcome_n']==1
    assert r['actual_draws_scored']==0 and r['macro_recall'] is None
    assert r['expected_draws_all_forecasts']==.3 and r['expected_draws_scored_forecasts']==0


def test_confusion_rows_actual_columns_predicted():
    rows=[{'probs_full':[.6,.2,.2],'outcome':0},
          {'probs_full':[.2,.5,.3],'outcome':1},
          {'probs_full':[.6,.2,.2],'outcome':2}]
    r=class_audit(rows)
    assert r['confusion_actual_rows_predicted_columns']==[[1,0,0],[0,1,0],[1,0,0]]
    assert r['by_class']['home']['precision']==.5
    assert r['by_class']['draw']['precision']==1.
    assert r['macro_recall']==pytest.approx(2/3)


@pytest.mark.parametrize('p',[[.4,.3,.3],[.2,.5,.3],[.2,.25,.55]])
def test_draw_pair_is_real_probability_sum_separate_from_top2(p):
    before=list(p);r=draw_view(p)
    assert r['pair_including_draw'].count('draw')==1
    indices=[('home','draw','away').index(k) for k in r['pair_including_draw']]
    assert r['pair_including_draw_coverage_full']==sum(p[i] for i in indices)
    assert r['top2_coverage_full']>=r['pair_including_draw_coverage_full']-1e-12
    assert r['top1']==('home','draw','away')[max(range(3),key=lambda i:p[i])]
    assert p==before


@pytest.mark.parametrize('bad',[[.4,.4,.4],[.4,float('nan'),.6],[-.1,.5,.6],[True,0,0]])
def test_invalid_probabilities_not_used(bad):
    with pytest.raises(ValueError):draw_view(bad)


def test_full_precision_draw_is_not_lost_after_rounding():
    precise=(.35001,.35002,.29997)
    with patch('engine.jingcai_core.shin_probs',return_value=precise):
        r=predict(payload(),dict(CONFIG,market_weight=1.),asof=ASOF)
    assert r['p_home']==r['p_draw']
    assert r['p_final_full'][1]>r['p_final_full'][0]
    assert r['recommendation']['direction']=='平局'
    assert r['recommendation']['pick_rule']=='argmax_full_precision'
    assert r['wdl_draw_view']['top1']=='draw'
    assert '单选首选平' in format_1x2(r)


def test_formatter_distinguishes_single_pick_and_two_outcome_reference():
    r=predict(payload(),CONFIG,asof=ASOF);before=deepcopy(r)
    s=format_1x2(r)
    assert '单选首选' in s and '平局' in s and '防平双选参考' in s and '双选另计' in s
    assert r==before


def test_form_draw_market_win_disagreement_is_visible():
    r=draw_view([.4,.3,.3],model=[.3,.4,.3],market=[.5,.25,.25])
    assert r['model_draw_top1'] and not r['market_draw_top1']
    assert r['model_market_draw_disagreement']
    text=format_1x2(dict(p_home=.4,p_draw=.3,p_away=.3,wdl_draw_view=r))
    assert '战绩模型首选平' in text and '市场首选胜' in text


def test_empty_metrics_are_unknown_not_perfect():
    r=class_audit([])
    assert r['macro_recall'] is None and r['scored_n']==0 and not r['zero_draw_top1_attention']
    assert all(v['precision'] is None and v['recall'] is None for v in r['by_class'].values())


@pytest.mark.parametrize('bad',[True,'draw',3,-1])
def test_invalid_actual_label_rejected(bad):
    with pytest.raises(ValueError):class_audit([{'probs_full':[.4,.3,.3],'outcome':bad}])
