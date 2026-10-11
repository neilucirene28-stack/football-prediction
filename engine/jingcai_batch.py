"""批量竞彩入口：官方整数让球进入predict；禁止从亚洲盘口推导竞彩让球。"""
from .jingcai_handicap import integer_handicap


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
