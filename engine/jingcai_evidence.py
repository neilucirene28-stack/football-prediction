"""防止无球队数据的进球先验被当作独立竞彩战绩信号。"""
import math

REVISION = 'jingcai-signal-evidence-v16'


def _number(value, *, minimum=0., strict=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        v = float(value)
    except (ValueError, OverflowError):
        return False
    return math.isfinite(v) and (v > minimum if strict else v >= minimum)


def _samples(rows, target):
    if not isinstance(rows, list):
        return {'declared': 0, 'usable': 0, 'valid': False}
    if any(not isinstance(r, dict) or any(isinstance(r.get(k), bool)
           or not isinstance(r.get(k), int) or r[k] < 0 for k in ('gf','ga')) for r in rows):
        return {'declared': len(rows), 'usable': 0, 'valid': False}
    def compatible(r):
        v = r.get('venue') or r.get('home_away')
        # 与输入规范化及estimate_lambdas消费的主客场规则一致。
        return v not in ('H','A') or v == target
    return {'declared': len(rows), 'usable': sum(compatible(r) for r in rows), 'valid': True}


def signal_evidence(payload):
    h = _samples(payload.get('home_recent') or [], 'H')
    a = _samples(payload.get('away_recent') or [], 'A')
    form = all(v['valid'] and v['declared'] >= 6 and v['usable'] > 0 for v in (h,a))
    odds = payload.get('odds')
    market = (isinstance(odds, dict) and all(_number(odds.get(k), minimum=1., strict=True)
              for k in ('home','draw','away')))
    elo = payload.get('elo')
    elo_ok = (isinstance(elo, dict) and all(_number(elo.get(k)) for k in ('home','away')))
    active = [name for name, ok in (('form',form), ('market',market), ('elo',elo_ok)) if ok]
    modes = {('form',): 'form_only', ('market',): 'market_only', ('elo',): 'elo_only',
             ('form','market'): 'form_market', ('form','elo'): 'form_elo',
             ('market','elo'): 'market_elo', ('form','market','elo'): 'form_market_elo'}
    return {'revision': REVISION, 'status': 'supported' if active else 'prior_only_blocked',
            'mode': modes.get(tuple(active),'prior_only_blocked'), 'form_available': form,
            'market_available': bool(market), 'elo_available': bool(elo_ok),
            'history': {'home':h, 'away':a}, 'active_input_signals': active,
            'source_lineage_verified': False,
            'qualification': 'valid declared inputs; not independent source authentication; no_data_is_not_zero'}


def supported_weights(weights, evidence):
    copied = dict(weights)
    for name, field in (('model','form_available'), ('market','market_available'), ('elo','elo_available')):
        if not evidence[field]: copied[name] = 0.
    total = sum(copied.values())
    if total <= 0: raise ValueError('配置未给可用比赛信号正权重，禁止先验兜底推荐')
    if copied == weights: return copied
    return {name:value/total for name,value in copied.items()}


def validate_forecast_evidence(payload, result, *, require_evidence=True):
    grade=result.get('grade'); score=result.get('completeness')
    if grade == 'D' or (score is not None and (not _number(score,minimum=40.)
                                              or float(score)>100)):
        raise ValueError('无数据或D级预测不能进入正式竞彩账本')
    if not require_evidence: return
    if grade not in ('S','A','B','C') or score is None:
        raise ValueError('正式竞彩预测缺少完整度证据')
    evidence=signal_evidence(payload)
    if evidence['status'] != 'supported':
        raise ValueError('无有效比赛数据，禁止把统一先验作为正式竞彩预测')
    declared=result.get('forecast_evidence')
    if declared is not None and declared != evidence:
        raise ValueError('预测数据证据与原输入不一致')
    weights=result.get('weights')
    if not isinstance(weights,dict) or any(not _number(weights.get(k)) for k in ('model','market','elo')):
        raise ValueError('正式竞彩预测缺少有效信号权重')
    if abs(sum(float(weights[k]) for k in ('model','market','elo'))-1)>0.00151:
        raise ValueError('正式竞彩预测信号权重未归一')
    for name,field in (('model','form_available'),('market','market_available'),('elo','elo_available')):
        if not evidence[field] and float(weights[name])>0:
            raise ValueError('无可用双队战绩时不得给先验模型正权重' if name=='model'
                             else f'无可用{name}证据时不得给其正权重')
