"""竞彩净胜球校准研究：仅离线，固定候选集，保留全场WDL边际。"""
from collections import Counter, defaultdict
import hashlib
import json
import math
from datetime import timezone

from .jingcai_handicap import handicap_outcome, integer_handicap
from .jingcai_ledger import validate_live_record
from .jingcai_review import decoded, prepared_record, timestamp

SCHEMA = "jingcai-margin-walkforward-v1"
SPEC = {"min_train_per_line": 100, "min_test": 100, "min_folds": 3,
        "log_multipliers": [0.0, -0.5, 0.5, -1.0, 1.0]}


def checked_matrix(matrix):
    if not isinstance(matrix, list) or len(matrix) < 2:
        raise ValueError("缺少完整比分矩阵")
    n = len(matrix)
    if any(not isinstance(row, list) or len(row) != n for row in matrix):
        raise ValueError("比分矩阵必须为方阵")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
           not math.isfinite(v) or v < 0 or v > 1 for row in matrix for v in row):
        raise ValueError("比分矩阵存在非法概率")
    if abs(sum(map(sum, matrix)) - 1) > 1e-8:
        raise ValueError("比分矩阵质量不为1")
    return matrix


def matrix_probabilities(matrix, rq):
    p = [0.0] * 3
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            p[handicap_outcome(i, j, rq)] += value
    return p


def tilt(matrix, rq, log_multiplier):
    """调整目标净胜球在对应WDL区间内的相对质量；不改变WDL总质量。"""
    checked_matrix(matrix); rq = integer_handicap(rq)
    if not math.isfinite(log_multiplier) or abs(log_multiplier) > 1:
        raise ValueError("候选系数超出研究范围")
    if log_multiplier == 0 or rq == 0:
        return [list(row) for row in matrix]
    old = matrix_probabilities(matrix, 0)
    factor = math.exp(log_multiplier)
    candidate = [[v * (factor if i-j == -rq else 1)
                  for j, v in enumerate(row)] for i, row in enumerate(matrix)]
    new = matrix_probabilities(candidate, 0)
    return [[v * (old[handicap_outcome(i,j,0)] / new[handicap_outcome(i,j,0)]
                  if new[handicap_outcome(i,j,0)] else 0)
             for j,v in enumerate(row)] for i,row in enumerate(candidate)]


def eligible_record(row, asof):
    try:
        prepared, reason = prepared_record(row, asof)
        if reason: return None, reason
        saved = decoded(row['payload']); payload, result = saved['input'], saved['result']
        times = validate_live_record(payload, result, now=prepared['predicted_at'], require_hash=True)
        if not row.get('source'): return None, 'missing_settlement_source'
        if result.get('p_final_full') is None: return None, 'missing_full_probability'
        matrix = checked_matrix(result.get('score_matrix_full'))
        h = result.get('derivatives', {}).get('handicap_1x2', {})
        rq = integer_handicap(payload.get('handicap_line'))
        if integer_handicap(h.get('line')) != rq: return None, 'mismatched_handicap_line'
        hp = [h[f'p_{k}_full'] for k in ('home','draw','away')]
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)
               or abs(a-v)>1e-8 for a,v in zip(matrix_probabilities(matrix,rq),hp)):
            return None, 'mismatched_handicap_matrix'
        if any(abs(a-b)>1e-8 for a,b in zip(matrix_probabilities(matrix,0),result['p_final_full'])):
            return None, 'mismatched_wdl_matrix'
        score = (row['home_goals'], row['away_goals'])
        return {**prepared, 'snapshot_at': times['snapshot'], 'rq': rq,
                'matrix': matrix, 'score': score}, None
    except (ValueError,TypeError,KeyError,OverflowError,AttributeError):
        return None, 'invalid_margin_bound_record'


def losses(row, matrix):
    p = matrix_probabilities(matrix,row['rq'])
    hg,ag = row['score']; actual = handicap_outcome(hg,ag,row['rq'])
    score_prob = matrix[hg][ag] if hg<len(matrix) and ag<len(matrix) else 0
    return {'brier':sum((v-int(i==actual))**2 for i,v in enumerate(p)),
            'logloss':-math.log(max(p[actual],1e-12)),
            'draw_brier':(p[1]-int(actual==1))**2,
            'draw_expected':p[1], 'draw_actual':int(actual==1),
            'score_logloss':-math.log(max(score_prob,1e-12)),
            'score_outside_grid':int(hg>=len(matrix) or ag>=len(matrix)),
            'goals_bias':sum((i+j)*v for i,r in enumerate(matrix) for j,v in enumerate(r))-hg-ag}


def summary(values):
    n=len(values)
    return {'n':n, **{k:sum(v[k] for v in values)/n if n else None
                     for k in ('brier','logloss','draw_brier','score_logloss','goals_bias')},
            **{k:sum(v[k] for v in values) for k in ('draw_expected','draw_actual','score_outside_grid')}}


def fit(rows):
    """每个官方盘口独立选参；样本不足保持0，0在并列时优先。"""
    groups=defaultdict(list)
    for row in rows: groups[row['rq']].append(row)
    params={}
    for rq, group in groups.items():
        theta=0.0
        if rq and len(group)>=SPEC['min_train_per_line']:
            theta=min(SPEC['log_multipliers'], key=lambda t:
                      sum(losses(r,tilt(r['matrix'],rq,t))['brier'] for r in group))
        params[rq]={'log_multiplier':theta,'train_n':len(group),
                    'fitted':bool(rq and len(group)>=SPEC['min_train_per_line'])}
    return params


def evaluate(rows, *, asof, test_start):
    asof=timestamp(asof,'asof'); test_start=timestamp(test_start,'test_start')
    if test_start>=asof: raise ValueError('测试起点必须早于复盘截止')
    excluded=Counter(); groups=defaultdict(list)
    for row in rows:
        if not isinstance(row,dict): excluded['invalid_row']+=1; continue
        r,reason=eligible_record(row,asof)
        if reason: excluded[reason]+=1
        else: groups[(r['model_version'],r['match_id'])].append(r)
    selected=[]
    for repeats in groups.values():
        if len({(r['score'],r['rq'],r['league'],r['kickoff_at']) for r in repeats})>1:
            excluded['conflicting_physical_match']+=len(repeats); continue
        chosen=min(repeats,key=lambda r:(r['predicted_at'],r['prediction_id']))
        chosen['settled_at']=max(r['settled_at'] for r in repeats)
        excluded['additional_forecast_same_match_version']+=len(repeats)-1
        selected.append(chosen)
    versions=defaultdict(list)
    for row in selected: versions[row['model_version']].append(row)
    output={}
    for version,cohort in sorted(versions.items()):
        days=defaultdict(list); folds=[]; baseline=[]; candidate=[]; improved_lines=set()
        for row in cohort:
            if row['kickoff_at']>=test_start:
                days[row['kickoff_at'].astimezone(timezone.utc).date()].append(row)
        for day,test in sorted(days.items()):
            cutoff=min(r['snapshot_at'] for r in test); kickoff=min(r['kickoff_at'] for r in test)
            ids={r['match_id'] for r in test}
            train=[r for r in cohort if r['settled_at']<cutoff and r['kickoff_at']<kickoff
                   and r['match_id'] not in ids]
            params=fit(train); b=[]; c=[]; trainable=set()
            for row in test:
                parameter=params.get(row['rq'],{'log_multiplier':0,'train_n':0,'fitted':False})
                if not parameter['fitted']: continue
                trainable.add(row['rq'])
                b.append(losses(row,row['matrix']))
                c.append(losses(row,tilt(row['matrix'],row['rq'],parameter['log_multiplier'])))
            fold={'test_day_utc':day.isoformat(),'train_n':len(train),'test_n':len(test),
                  'evaluated_test_n':len(b),'untrained_line_test_n':len(test)-len(b),
                  'training_cutoff':cutoff.isoformat(),
                  'training_latest_settlement':max((r['settled_at'] for r in train),default=None),
                  'parameters':{str(k):v for k,v in params.items()},
                  'status':'evaluated' if trainable else 'insufficient_training',
                  'baseline':summary(b),'candidate':summary(c)}
            if fold['training_latest_settlement'] is not None:
                fold['training_latest_settlement']=fold['training_latest_settlement'].isoformat()
            folds.append(fold)
            if trainable:
                improved_lines.update(trainable); baseline.extend(b); candidate.extend(c)
        sb,sc=summary(baseline),summary(candidate); reasons=[]
        if len(candidate)<SPEC['min_test']: reasons.append('insufficient_test_matches')
        if sum(f['status']=='evaluated' for f in folds)<SPEC['min_folds']:
            reasons.append('insufficient_temporal_folds')
        if not candidate or not any(f['parameters'][str(k)]['log_multiplier']!=0
                 for f in folds for k in improved_lines if str(k) in f['parameters']):
            reasons.append('no_nonzero_candidate_evaluated')
        for key in ('brier','logloss','draw_brier','score_logloss'):
            if not candidate or sc[key]>sb[key]+1e-12: reasons.append(key+'_worse_or_unavailable')
        if not candidate or abs(sc['goals_bias'])>abs(sb['goals_bias'])+1e-12:
            reasons.append('goal_mean_bias_worse_or_unavailable')
        if any(f['candidate']['brier']>f['baseline']['brier']+1e-12 for f in folds
               if f['status']=='evaluated'): reasons.append('some_temporal_fold_brier_worse')
        by_line={str(rq):summary([losses(r,r['matrix']) for r in cohort if r['rq']==rq])
                 for rq in sorted({r['rq'] for r in cohort})}
        output[version]={'eligible_matches':len(cohort),'descriptive_by_line':by_line,
                         'folds':folds,'baseline':sb,'candidate':sc,'blocking_reasons':reasons,
                         'research_screen_passed':not reasons,'production_enabled':False}
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,default=str,
                                    separators=(',',':')).encode()).hexdigest()
    return {'schema':SCHEMA,'scope':'jingcai_only','status':'ok' if selected else 'no_eligible_data',
            'input_rows':len(rows),'eligible_matches':len(selected),'excluded':dict(excluded),
            'asof':asof.isoformat(),'test_start':test_start.isoformat(),'spec':SPEC,
            'input_sha256':digest,'by_version':output,'production_enabled':False,
            'qualification':'offline screening only; not source authentication or independent holdout; '
                            'WDL mass preserved, goal mean may change and is gated; no production parameters written'}
