"""批量竞彩入口：官方整数让球进入predict；禁止从亚洲盘口推导竞彩让球。"""
from .jingcai_handicap import integer_handicap
from .jingcai_scope import validated_config
from .errors import PredictError
from .jingcai_draw import class_audit
from copy import deepcopy


def with_official_handicap(payload, fixture):
    p = dict(payload)
    if fixture.get("rq") is not None:
        rq = integer_handicap(fixture["rq"])
        if p.get("handicap_line") is not None and integer_handicap(p["handicap_line"]) != rq:
            raise ValueError("官方竞彩让球与已提供handicap_line冲突")
        p["handicap_line"] = rq
    if fixture.get("handicap_sp") is not None:
        p["handicap_sp"] = fixture["handicap_sp"]
    return p


def handicap_output(result, *, include_rq_alias=False):
    h = (result.get("derivatives") or {}).get("handicap_1x2")
    if h is None:
        return None
    out = dict(h)
    if include_rq_alias:
        out["rq"] = out["line"]
    return out


def predict_batch(records, config=None, *, asof=None):
    """无数据列入abstained；只读计算，不构造兜底概率，不写正式账本。

    输入每行包含number、payload；所有项目范围先校验，再开始计算。
    asof重放会保留核心的historical_replay标签。
    """
    from .jingcai_predictor import predict
    if not isinstance(records, list):
        raise PredictError('批量输入必须为records列表')
    seen=set()
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get('payload'), dict):
            raise PredictError('批量每行必须含payload对象')
        number=row.get('number')
        if not isinstance(number, str) or not number.strip() or number in seen:
            raise PredictError('批量场次编号必须为非空唯一字符串')
        seen.add(number)
        validated_config(row['payload'],config)
    accepted=[];abstained=[]
    for row in records:
        try:
            result=predict(row['payload'],config,asof=asof)
        except PredictError as exc:
            abstained.append({'number':row['number'],'status':'invalid_input','reason':str(exc)})
            continue
        if result['status'] != 'ok':
            abstained.append({'number':row['number'],**result})
            continue
        accepted.append({'number':row['number'],'input':deepcopy(row['payload']),'prediction':result})
    audit=class_audit([{'probs_full':row['prediction']['p_final_full'],'outcome':None} for row in accepted])
    return {'project':'jingcai','evaluation_mode':'historical_replay' if asof is not None else 'live',
            'requested_n':len(records),'predicted_n':len(accepted),'abstained_n':len(abstained),
            'records':accepted,'abstained':abstained,'wdl_classification':audit,
            'persistence':{'status':'not_saved','reason':'calculation_only_use_live_ledger_for_sealing'}}
