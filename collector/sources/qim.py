"""7M体育 (7m.com.cn) 接入 — 天气/开球时间/阵容/亚盘/战绩/H2H

纯静态 JS 数据，无反爬，无需 key。

数据源:
- 比赛ID映射: search.7m.com.cn/liveq.aspx?k={队名}&e=0&js=1（返回当日比赛
  loadgame([[mid, home, away, score, state], ...])）
- 未来赛程: data.7m.com.cn/fixture_data/gb_{N}.js（N=1..7 为明日起 N 天后）
- 单场数据: analyse.7m.com.cn/{mid}/data/*.js
    gameinfo_gb.js      开球时间/球队/排名/亚盘/天气代码/温度
    gamelineup_gb.js    首发+替补（含球衣号、位置）
    gamehistory_gb.js   H2H（并行数组）
    gameteamhistory_gb.js 双方战绩（并行数组）
    gameoddsway_gb.js   同赔率走势（备用）

用途:
- find_mid(home, away): 按队名找 7M mid（自动处理繁简体、港式译名别名）
- get_match_snapshot(mid): 单场一次抓全（info + lineup）
- get_game_info / get_lineup / get_h2h / get_form: 分项抓取

注意:
- 搜索结果混用简繁体（法國/法国、波斯尼亞=波黑），模块内统一归一化
- 阵容赛前数小时才公布；未公布时 players 为空 → 记 missing，不硬编
- 天气代码查 WEATHER_ARR（取自 static.7m.com.cn/js/analyse/const/gb.js）
- odds.7m.com.cn 多家指数子域 403，不碰；主盘口在 gameinfo.handicap
"""

import re
import time
import urllib.parse

from _http import fetch_with_retry, HTTPError

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
POLITE_DELAY = 1.5

ANALYSE = "https://analyse.7m.com.cn"
SEARCH = "https://search.7m.com.cn/liveq.aspx"

# 天气代码 → 中文（static.7m.com.cn/js/analyse/const/gb.js，1-indexed）
WEATHER_ARR = ["", "晴天", "少云", "多云", "阴天", "小雨", "中到大雨", "雷阵雨",
               "雷暴", "小雪", "大雨", "晴天", "晴间多云", "少云", "多云",
               "雨加雪", "", "", "晴间多云", "小雷雨", "小阵雨", "汽雾", "冻雾",
               "零星小雨", "中雨", "小阵雪", "细雨", "阵雪", "风尘", "低空飘雪",
               "大阵雪", "中雪"]

POS_MAP = {"0": "门将", "1": "后卫", "2": "中场", "3": "前锋"}
STATUS_MAP = {"3": "首发", "0": "替补"}

# 繁体 → 简体（7M 搜索结果混用简繁体）
_TRAD2SIMP = str.maketrans({
    "國": "国", "脫": "脱", "維": "维", "亞": "亚", "馬": "马", "爾": "尔",
    "羅": "罗", "烏": "乌", "茲": "兹", "別": "别", "麼": "么", "絲": "丝",
    "蘭": "兰", "奧": "奥", "愛": "爱", "華": "华", "門": "门", "裡": "里",
    "龍": "龙", "韋": "韦", "漢": "汉", "頓": "顿", "徹": "彻", "納": "纳",
    "達": "达", "魯": "鲁", "薩": "萨", "幾": "几", "東": "东", "雲": "云",
    "電": "电", "話": "话", "時": "时", "間": "间", "開": "开", "關": "关",
    "隊": "队", "員": "员", "賽": "赛", "聯": "联", "盃": "杯", "歐": "欧",
    "錦": "锦", "標": "标", "軍": "军", "勝": "胜", "負": "负", "進": "进",
    "積": "积", "剋": "克", "衞": "卫", "盧": "卢", "傑": "杰", "歷": "历",
    "蘇": "苏", "貝": "贝", "萊": "莱", "裡": "里", "嚤": "摩", "內": "内",
    "爾": "尔",
})

# 7M 港式译名 → 通用简体队名
TEAM_ALIASES = {
    "波斯尼亚": "波黑",
    "阿美尼亚": "亚美尼亚",
    # 以下为 7M 港式译名，2026-10-08 按 search.7m.com.cn 实测逐条验证
    # （7M 用繁体+港式音译，如山度士=桑托斯；norm_team 先繁→简再走本表）
    "VPS华沙": "瓦萨",          # 7M: VPS華沙
    "古比斯": "库奥皮奥",        # 7M: 古比斯 (KuPS)
    "山度士": "桑托斯",          # 7M: 山度士 (Santos)
    "法林明高": "弗拉门戈",      # 7M: 法林明高 (Flamengo)
    "帕拉尼恩斯": "巴竞技",      # 7M: 帕拉尼恩斯 (Athletico Paranaense；澳客简称巴竞技)
    "明尼路": "米竞技",          # 7M: 明尼路 (Atlético Mineiro；澳客简称米竞技)
    "富明尼斯": "弗鲁米嫩",      # 7M: 富明尼斯 (Fluminense；澳客简称弗鲁米嫩)
    "哥列迪巴": "科里蒂巴",      # 7M: 哥列迪巴 (Coritiba)
    "彭美拉斯": "帕梅拉斯",      # 7M: 彭美拉斯 (Palmeiras；澳客简称帕梅拉斯)
    "巴希亚": "巴伊亚",            # 7M: 巴希亞 (Bahia；澳客作巴伊亚)
}


def norm_team(name):
    """队名归一化：繁→简 + 别名映射。"""
    n = (name or "").translate(_TRAD2SIMP).strip()
    return TEAM_ALIASES.get(n, n)


def _get_text(url):
    time.sleep(POLITE_DELAY)
    raw = fetch_with_retry(url, headers=HEADERS, timeout=25, parse_json=False)
    return raw.decode("utf-8", "ignore")


def _get_js_var(url):
    """取 var xxx = {...}; 返回去掉 var 声明后的 JSON 文本（双引号 JSON）。"""
    txt = _get_text(url)
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
    if not m:
        raise ValueError(f"7M JS 解析失败: {url}")
    return m.group(1)


def search_match(keyword):
    """按关键词搜当日比赛。

    返回 [{'mid': str, 'home': str, 'away': str, 'score': str, 'state': str}]
    state '17' = 未开球。
    """
    import json
    url = SEARCH + "?k=" + urllib.parse.quote(keyword) + "&e=0&js=1"
    txt = _get_text(url)
    m = re.search(r"loadgame\(\[(.*)\]\);", txt, re.S)
    if not m:
        return []
    rows = json.loads("[" + m.group(1) + "]")
    return [{"mid": r[0], "home": r[1], "away": r[2],
             "score": r[3], "state": r[4]} for r in rows]


def _is_senior(row):
    t = row["home"] + row["away"]
    return "(U" not in t and "女足" not in t and "U1" not in t


def find_mid(home, away):
    """按（主，客）队名找 7M mid。

    先用主队名搜，再用归一化队名精确匹配；过滤 U 系列/女足。
    找不到返回 None。
    """
    cands = []
    nh, na = norm_team(home), norm_team(away)
    # 搜索关键词：主客队名 + 其 7M 侧别名。7M 按关键词搜当日比赛，
    # 主队名若是 7M 无结果的叫法（如巴竞技→帕拉尼恩斯），用别名/客队名补搜。
    # 2026-10-08 加。
    keywords = [home, away]
    for alias_7m, std in TEAM_ALIASES.items():
        if (std == nh or std == na) and alias_7m not in keywords:
            keywords.append(alias_7m)
    seen = set()
    for kw in keywords:
        try:
            for r in search_match(kw):
                if r["mid"] not in seen:
                    seen.add(r["mid"])
                    cands.append(r)
        except Exception:
            continue
    for r in cands:
        if not _is_senior(r):
            continue
        if norm_team(r["home"]) == nh and norm_team(r["away"]) == na:
            return r["mid"]
    # 主客队名可能互换（中立场），再宽松试一次
    for r in cands:
        if not _is_senior(r):
            continue
        if {norm_team(r["home"]), norm_team(r["away"])} == {nh, na}:
            return r["mid"]
    return None


def get_game_info(mid):
    """单场基本信息：开球时间/球队/排名/亚盘/天气。

    返回 dict，抓不到的字段为 None（不硬编）。
    """
    import json
    from datetime import datetime, timezone, timedelta
    d = json.loads(_get_js_var(f"{ANALYSE}/{mid}/data/gameinfo_gb.js"))
    kickoff = None
    try:
        kickoff = (datetime.fromtimestamp(
            int(d.get("time", 0)) / 1000,
            tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"))
    except (TypeError, ValueError):
        pass
    wcode = d.get("weather")
    try:
        wtext = WEATHER_ARR[int(wcode)] or None
    except (TypeError, ValueError, IndexError):
        wtext = None
    return {
        "mid": str(mid),
        "kickoff_beijing": kickoff,
        "league": d.get("mname"),
        "home": d.get("taname"), "home_id": d.get("taid"),
        "home_rank": d.get("tarank"), "home_record": d.get("tadata"),
        "away": d.get("tbname"), "away_id": d.get("tbid"),
        "away_rank": d.get("tbrank"), "away_record": d.get("tbdata"),
        "neutral": d.get("neutral") == "1",
        "asian_handicap": d.get("handicap"),
        "weather_code": wcode,
        "weather": wtext,
        "temperature": d.get("temperature"),
        "source": "7m",
    }


def _parse_player(m):
    pid, name, pos, status = m.groups()
    shirt, pname = None, name.strip()
    sm = re.match(r"^(\d+)\s+(.+)$", pname)
    if sm:
        shirt, pname = sm.group(1), sm.group(2)
    return {"id": pid, "shirt": shirt, "name": pname,
            "pos": POS_MAP.get(pos, pos),
            "status": STATUS_MAP.get(status, "未知")}


def get_lineup(mid):
    """单场阵容：双方首发 11 人 + 替补名单。

    返回 {'home_starters': [...], 'home_subs': [...],
            'away_starters': [...], 'away_subs': [...]}，
    每人 {id, shirt, name, pos, status}。
    阵容未公布时各列表为空。
    """
    txt = _get_text(f"{ANALYSE}/{mid}/data/gamelineup_gb.js")
    out = {"home_starters": [], "home_subs": [],
           "away_starters": [], "away_subs": [],
           "home_formation": None, "away_formation": None,
           "home_avg_age": None, "away_avg_age": None}
    for side, key in (("A", "home"), ("B", "away")):
        m = re.search(r"'%s':\[(.*?)\](?=,|\}\s*;?\s*$)" % side, txt, re.S)
        if not m:
            continue
        for pm in re.finditer(
                r"\{'id':'([^']*)','n':'([^']*)','p':'([^']*)','s':'([^']*)'\}",
                m.group(1)):
            p = _parse_player(pm)
            if p["status"] == "首发":
                out[f"{key}_starters"].append(p)
            elif p["status"] == "替补":
                out[f"{key}_subs"].append(p)
    for side, key in (("A", "home"), ("B", "away")):
        fm = re.search(r"'%sformation':'([^']*)'" % side, txt)
        ag = re.search(r"'%sage':'([^']*)'" % side, txt)
        if fm:
            out[f"{key}_formation"] = fm.group(1)
        if ag:
            out[f"{key}_avg_age"] = ag.group(1)
    return out


def _zip_history(d):
    """把并行数组格式的战绩/H2H 压成 list[dict]。"""
    keys = ("id", "mid", "aid", "bid", "date", "liveA", "liveB",
            "redA", "redB", "bc", "ng", "rq")
    cols = {k: d.get(k, []) for k in keys}
    n = min((len(v) for v in cols.values() if isinstance(v, list)),
            default=0)
    rows = []
    for i in range(n):
        r = {k: cols[k][i] for k in keys}
        try:
            y, mth, day = r["date"].split("-")
            r["date"] = f"20{y}-{mth}-{day}"
        except (ValueError, AttributeError):
            pass
        rows.append(r)
    return rows


def get_h2h(mid):
    """两队交锋记录（list[dict]，含比分/半场/亚盘）。"""
    import json
    d = json.loads(_get_js_var(f"{ANALYSE}/{mid}/data/gamehistory_gb.js"))
    return _zip_history(d.get("historymatch", {}))


def get_form(mid):
    """双方战绩：{'home': [...], 'away': [...]}（list[dict]）。"""
    import json
    d = json.loads(_get_js_var(f"{ANALYSE}/{mid}/data/gameteamhistory_gb.js"))
    out = {}
    for side, key in (("A", "home"), ("B", "away")):
        out[key] = _zip_history(d.get(side, {}).get("all", {}).get("history", {}))
    return out


def get_match_snapshot(mid):
    """单场快照：info + lineup 一次抓全（供预测管线直接用）。

    阵容抓失败时记 missing（lineup_ok=False），不抛异常中断整场。
    """
    info = get_game_info(mid)
    try:
        info["lineup"] = get_lineup(mid)
        info["lineup_ok"] = bool(info["lineup"]["home_starters"]
                                 or info["lineup"]["away_starters"])
    except Exception as e:
        info["lineup"] = {"home_starters": [], "home_subs": [],
                          "away_starters": [], "away_subs": [],
                          "home_formation": None, "away_formation": None,
                          "home_avg_age": None, "away_avg_age": None,
                          "error": str(e)[:120]}
        info["lineup_ok"] = False
    return info


if __name__ == "__main__":
    print("=== 7M 搜索+快照测试 ===")
    mid = find_mid("法国", "比利时")
    print("find_mid 法国vs比利时:", mid)
    snap = get_match_snapshot(mid)
    print("开球:", snap["kickoff_beijing"], "| 天气:", snap["weather"],
          snap["temperature"], "| 亚盘:", snap["asian_handicap"])
    print("首发:", len(snap["lineup"]["home_starters"]),
          len(snap["lineup"]["away_starters"]),
          "| 替补:", len(snap["lineup"]["home_subs"]),
          len(snap["lineup"]["away_subs"]))
    print("H2H:", len(get_h2h(mid)), "| 战绩:",
          {k: len(v) for k, v in get_form(mid).items()})
