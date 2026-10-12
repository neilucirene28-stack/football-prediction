"""竞彩平局解释和类别诊断；不改概率、不强制选择平局。"""
import math

REVISION='jingcai-draw-class-audit-v15'
OUTCOMES=('home','draw','away')


def checked_probabilities(values):
    if not isinstance(values,(list,tuple)) or len(values)!=3:
        raise ValueError('胜平负必须为三向概率')
    if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v)
           or v<0 or v>1 for v in values) or abs(sum(values)-1)>1e-8:
        raise ValueError('胜平负概率必须有限、非负且加总为1')
    return tuple(float(v) for v in values)


def draw_view(values,model=None,market=None):
    p=checked_probabilities(values)
    ranked=sorted(range(3),key=lambda i:(-p[i],i));top,second,third=ranked
    nondraw=max((0,2),key=lambda i:(p[i],-i))
    pair=(top,nondraw) if top==1 else (top,1)
    model_top=None if model is None else max(range(3),key=lambda i:checked_probabilities(model)[i])
    market_top=None if market is None else max(range(3),key=lambda i:checked_probabilities(market)[i])
    return {'probabilities_full':dict(zip(OUTCOMES,p)),'top1':OUTCOMES[top],
        'top1_probability_full':p[top],'pick_rule':'argmax_full_precision',
        'draw_probability_full':p[1],'draw_rank':ranked.index(1)+1,
        'top1_gap_full':p[top]-p[second],
        'top2':[OUTCOMES[i] for i in ranked[:2]],'top2_coverage_full':p[top]+p[second],
        'top2_excluded':OUTCOMES[third],'top2_excluded_probability_full':p[third],
        'pair_including_draw':[OUTCOMES[i] for i in pair],
        'pair_including_draw_coverage_full':sum(p[i] for i in pair),
        'model_draw_top1':model_top==1 if model is not None else None,
        'market_draw_top1':market_top==1 if market is not None else None,
        'model_top1':OUTCOMES[model_top] if model_top is not None else None,
        'market_top1':OUTCOMES[market_top] if market_top is not None else None,
        'model_market_draw_disagreement':model_top is not None and market_top is not None
            and (model_top==1)!=(market_top==1),
        'policy':'two-outcome draw coverage is separate from a single prediction; no probability changes'}


def class_audit(rows):
    """rows={probs_full,outcome?}；未知赛果不填成胜/平/负，空精度保留None。"""
    cm=[[0,0,0] for _ in range(3)];forecast_counts=[0,0,0]
    forecast_n=scored_n=missing=0;expected=0.;expected_scored=0.
    for row in rows:
        p=checked_probabilities(row['probs_full']);pick=max(range(3),key=lambda i:p[i])
        forecast_n+=1;forecast_counts[pick]+=1;expected+=p[1]
        actual=row.get('outcome')
        if actual is None:missing+=1;continue
        if isinstance(actual,bool) or not isinstance(actual,int) or actual not in (0,1,2):
            raise ValueError('实际胜平负须为0/1/2或未知None')
        cm[actual][pick]+=1;scored_n+=1;expected_scored+=p[1]
    actual_counts=[sum(row) for row in cm]
    pred_counts=[sum(row[i] for row in cm) for i in range(3)]
    by_class={}
    for i,name in enumerate(OUTCOMES):
        by_class[name]={'actual':actual_counts[i],'predicted':pred_counts[i],'correct':cm[i][i],
            'precision':cm[i][i]/pred_counts[i] if pred_counts[i] else None,
            'recall':cm[i][i]/actual_counts[i] if actual_counts[i] else None}
    recall=[v['recall'] for v in by_class.values() if v['recall'] is not None]
    return {'forecast_n':forecast_n,'scored_n':scored_n,'missing_outcome_n':missing,
        'outcome_order':list(OUTCOMES),'forecast_top1_counts':dict(zip(OUTCOMES,forecast_counts)),
        'expected_draws_all_forecasts':expected,'expected_draws_scored_forecasts':expected_scored,
        'actual_draws_scored':actual_counts[1],
        'confusion_actual_rows_predicted_columns':cm,'by_class':by_class,
        'macro_recall':sum(recall)/len(recall) if recall else None,
        'zero_draw_top1_attention':forecast_n>0 and forecast_counts[1]==0 and expected>=1,
        'qualification':'expected draw count is probability sum; not a required number of draw picks; prediction sets are not single-pick hits'}
