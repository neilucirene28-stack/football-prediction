"""
API-Football /predictions 接入（theopenmodel 替代，2026-10-08）

https://www.api-football.com/documentation-v3#tag/Predictions
免费档与 fixtures 共享 100次/天；本接口按 fixture_id 每次消耗1次。
用途：第4个独立信号 / 独立模型分歧检测（与引擎三向概率对照）。
Key 复用 apifootball.py 的 .af_key（600权限，不进git）。
"""

from apifootball import _request


# 板块中文联赛名 -> API-Football 英文联赛名（board ↔ AF 对齐用）。
# 注意：意甲/巴西甲在AF都叫"Serie A"，靠开球时间区分（时区不同极少撞车）；
# 未覆盖到的联赛在 daily_fetch 里记 unmapped_leagues，按需增补。
BOARD_LEAGUE_TO_AF = {
    "英超": "Premier League",
    "英冠": "Championship",
    "英甲": "League One",
    "英乙": "League Two",
    "西甲": "La Liga",
    "西乙": "Segunda División",
    "意甲": "Serie A",
    "意乙": "Serie B",
    "德甲": "Bundesliga",
    "德乙": "2. Bundesliga",
    "法甲": "Ligue 1",
    "法乙": "Ligue 2",
    "荷甲": "Eredivisie",
    "葡超": "Primeira Liga",
    "巴西甲": "Serie A",
    "巴西乙": "Serie B",
    "美职": "Major League Soccer",
    "墨联": "Liga MX",
    "J1": "J1 League",
    "J2": "J2 League",
    "韩K1": "K League 1",
    "韩K2": "K League 2",
    "中超": "Super League",
    "芬超": "Veikkausliiga",
    "挪超": "Eliteserien",
    "瑞典超": "Allsvenskan",
    "丹超": "Superliga",
    "比甲": "Pro League",
    "奥超": "Bundesliga",
    "瑞士超": "Super League",
    "土超": "Süper Lig",
    "希腊超": "Super League 1",
    "罗甲": "Liga I",
    "波兰超": "Ekstraklasa",
}


def _pct(s):
    """'45%' -> 0.45，解析失败返回 None。"""
    try:
        return round(float(str(s).strip().rstrip("%")) / 100.0, 4)
    except (TypeError, ValueError, AttributeError):
        return None


def get_predictions(fixture_id):
    """
    获取单场第三方模型预测。
    返回 {"p_home","p_draw","p_away","advice"}（概率0-1，已归一化）；
    无数据/失败返回 None，不抛异常。
    """
    try:
        data = _request("/predictions", {"fixture": fixture_id})
    except Exception:
        return None
    resp = data.get("response") or []
    if not resp:
        return None
    preds = resp[0].get("predictions") or {}
    pct = preds.get("percent") or {}
    p_home = _pct(pct.get("home"))
    p_draw = _pct(pct.get("draw"))
    p_away = _pct(pct.get("away"))
    if p_home is None or p_draw is None or p_away is None:
        return None
    tot = p_home + p_draw + p_away
    if tot <= 0:
        return None
    return {
        "p_home": round(p_home / tot, 4),
        "p_draw": round(p_draw / tot, 4),
        "p_away": round(p_away / tot, 4),
        "advice": (preds.get("advice") or "").strip() or None,
    }


def get_predictions_bulk(fixture_ids, max_n=40):
    """
    批量拉取（配额守卫由调用方做）。每个 fixture 独立 try/except，
    单个失败不影响其他。返回 {fixture_id: {...} or None}。
    """
    out = {}
    for fid in list(fixture_ids)[:max_n]:
        try:
            out[fid] = get_predictions(fid)
        except Exception:
            out[fid] = None
    return out


def load_afb_lookup(day_dir):
    """
    读当日 afb_predictions.json，返回 {(board_home, board_away): [pH, pD, pA]}。

    只有无歧义对齐的条目（带 board_home/board_away）才进入；同联赛同时开球的
    多场在 daily_fetch 侧已跳过中文名，这里查不到就回 None，调用方静默跳过。
    供 predict 脚本组装 payload 的 af_pred 字段用；board 名是 okooo 中文名，
    与 500.com 有细微差异时（如"布拉干RB"vs"布拉干蒂诺RB"）调用方做模糊匹配。
    """
    import json
    import os

    fp = os.path.join(day_dir, "afb_predictions.json")
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    out = {}
    for _fid, x in (d.get("matches") or {}).items():
        bh, ba = x.get("board_home"), x.get("board_away")
        if bh and ba:
            out[(bh, ba)] = [x["p_home"], x["p_draw"], x["p_away"]]
    return out
