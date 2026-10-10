"""澳客网(www.okooo.com)接入 — 战绩/H2H/即时欧指/亚盘

静态 HTML 可抓，免费，无需 key。

覆盖: /soccer/match/{mid}/history/（两队交锋/双方近10场战绩/联赛排名/
      每场终指欧赔+亚盘；首行为本场即时指数：99家平均欧指+365亚盘）
用途:
- get_board_map(board): 当前对阵页全部 {(home, away): mid}（一次抓取）
- find_mid(home, away, board, _board_map): 按队名找 mid
- get_match_history(mid): 单场全部 history 数据

注意:
- /odds/ 页欧赔数据走 AJAX，静态抓不到；history 页首行即时指数已够用
- /ah/、/overunder/、/qingbao/ 子页反爬 405（时好时坏），不依赖
- 405 为 WAF 误伤，重试通常可过；模块内对 405 做有限重试
- 大小球、欧赔/亚盘初盘、阵容/伤停、xG：澳客静态无 → 如实缺失

防泄漏: history 页只取已完赛行（比分形如 2-1）；本场行（比分 '-'）只取即时指数。
"""

import re
import time

from _http import fetch_with_retry, HTTPError

BASE_URL = "https://www.okooo.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
POLITE_DELAY = 4  # 澳客反爬敏感，请求间隔保守


def _get_html(url, retries=3):
    """带 405 重试的抓取（澳客 WAF 偶发 405，重试通常可过）。"""
    last = None
    for _ in range(retries):
        time.sleep(POLITE_DELAY)
        try:
            raw = fetch_with_retry(url, headers=HEADERS, timeout=30,
                                   parse_json=False)
            return raw.decode("gb2312", "ignore")
        except HTTPError as e:
            last = e
            if getattr(e, "code", None) != 405:
                raise
            time.sleep(8)
    raise last


def _clean(s):
    return re.sub(r"<[^>]+>", "", s).strip()


def get_board_map(board="jingcai"):
    """当前对阵页全部场次 {(home, away): mid}，一次抓取供多次查询。"""
    html = _get_html(f"{BASE_URL}/{board}/")
    out = {}
    seen = set()
    # 按行块切分：每场一行，行 div 带 data-morder（主客队全名在行内 .zhum 的 title，
    # 主队在前、客队在后；联赛名在行内 .saiming）。
    # 2026-10-08 修：此前用 data-mid 起 6000 字符窗口取前两个 zhum，页面过渡态下
    # 联赛头曾混入 zhum（如 ("芬超","赫尔火花")），且窗口可跨行串到下一场，
    # 导致 7M 整节 0 映射。行块内取 + 联赛名兜底剔除，不再依赖联赛黑名单。
    row_starts = list(re.finditer(r'<div[^>]*data-morder="\d+"[^>]*>', html))
    for i, rs in enumerate(row_starts):
        block_end = row_starts[i + 1].start() if i + 1 < len(row_starts) else len(html)
        block = html[rs.start():block_end]
        mm = re.search(r'data-mid="(\d+)"', rs.group(0))
        if not mm:
            continue
        mid = mm.group(1)
        if mid in seen:
            continue
        seen.add(mid)
        teams = []
        for t in re.findall(r'class="zhum[^"]*" title="([^"]+)"', block):
            if t not in teams:
                teams.append(t)
        lg = re.search(r'class="saiming[^"]*"[^>]*title="([^"]+)"', block)
        if lg and lg.group(1) in teams:
            teams.remove(lg.group(1))
        if len(teams) >= 2:
            out[(teams[0], teams[1])] = mid
    return out


def get_board_map_with_league(board="jingcai"):
    """当前对阵页全部场次 {(home, away): (mid, league)}，一次抓取供多次查询。

    与 get_board_map 相同的行块解析，但额外保留联赛名（.saiming 的 title），
    供需要按联赛区分的调用方（如 ESPN summary 需要联赛 slug）使用。
    联赛名取不到时为 None。
    """
    html = _get_html(f"{BASE_URL}/{board}/")
    out = {}
    seen = set()
    row_starts = list(re.finditer(r'<div[^>]*data-morder="\d+"[^>]*>', html))
    for i, rs in enumerate(row_starts):
        block_end = row_starts[i + 1].start() if i + 1 < len(row_starts) else len(html)
        block = html[rs.start():block_end]
        mm = re.search(r'data-mid="(\d+)"', rs.group(0))
        if not mm:
            continue
        mid = mm.group(1)
        if mid in seen:
            continue
        seen.add(mid)
        teams = []
        for t in re.findall(r'class="zhum[^"]*" title="([^"]+)"', block):
            if t not in teams:
                teams.append(t)
        lg = re.search(r'class="saiming[^"]*"[^>]*title="([^"]+)"', block)
        league_name = lg.group(1) if lg else None
        if league_name and league_name in teams:
            teams.remove(league_name)
        if len(teams) >= 2:
            out[(teams[0], teams[1])] = (mid, league_name)
    return out


def find_mid(home, away, board="jingcai", _board_map=None):
    """在对阵页按队名找澳客 mid。

    board: 'jingcai' | 'beidan'。只取当前对阵页（日期页 /{board}/YYYY-MM-DD
    会 301 跳到带斜杠地址再 405，反爬拦截；日常管线在销售日当天跑，
    当前页即当日赛程）。
    返回 mid 字符串；找不到返回 None。
    """
    bm = _board_map if _board_map is not None else get_board_map(board)
    return bm.get((home, away))


def get_match_history(okooo_mid):
    """单场 history 数据。

    返回:
    {
      'form': {team: [{league,date,home,score,away,ht,
                       ou_1,ou_x,ou_2,ah_home_water,ah_line,ah_away_water}, ...10场]},
      'h2h':  [...10场],
      'current_odds': {'avg_1x2': [1,X,2], 'ah_365': {home_water,line,away_water},
                       'note': ...} 或 None,
      'source': 'okooo',
    }
    """
    html = _get_html(f"{BASE_URL}/soccer/match/{okooo_mid}/history/")
    out = {"form": {}, "h2h": [], "current_odds": None, "source": "okooo"}
    tables = list(re.finditer(r"<table[^>]*>(.*?)</table>", html, re.S))
    for t in tables:
        body = t.group(1)
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)
        if len(rows) < 2:
            continue
        hdr = _clean(rows[0])[:60]
        is_h2h = "两队交锋" in hdr
        is_form = "全赛事" in hdr
        if not (is_h2h or is_form):
            continue
        team = None
        if is_form:
            m = re.match(r"(\S+?)\s+全赛事", hdr)
            team = m.group(1) if m else None
        recs = []
        for r in rows[1:]:
            cells = [_clean(c) for c in
                     re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
            if len(cells) < 12:
                continue
            dm = re.search(r"(\d\d-\d\d-\d\d)\s+(\d\d:\d\d)\s*$", cells[1])
            datestr = ("20" + dm.group(1) + " " + dm.group(2)) if dm else cells[1]
            recs.append({
                "league": cells[0], "date": datestr,
                "home": cells[2], "score": cells[3], "away": cells[4],
                "ht": cells[5],
                "ou_1": cells[6] or None, "ou_x": cells[7] or None,
                "ou_2": cells[8] or None,
                "ah_home_water": cells[9] or None, "ah_line": cells[10] or None,
                "ah_away_water": cells[11] or None,
            })
        upcoming = [x for x in recs if x["score"] == "-"]
        done = [x for x in recs if re.match(r"^\d+-\d+$", x["score"])]
        if is_h2h:
            out["h2h"] = done[:10]
            if upcoming and out["current_odds"] is None:
                u = upcoming[0]
                out["current_odds"] = {
                    "avg_1x2": [u["ou_1"], u["ou_x"], u["ou_2"]],
                    "ah_365": {"home_water": u["ah_home_water"],
                               "line": u["ah_line"],
                               "away_water": u["ah_away_water"]},
                    "note": "history页首行即时指数（99家平均欧指+365亚盘）",
                }
        elif is_form and team:
            out["form"][team] = done[:10]
    return out


if __name__ == "__main__":
    print("=== 澳客 history 测试 ===")
    mid = find_mid("法国", "比利时", "jingcai")
    print("find_mid 法国vs比利时:", mid)
    d = get_match_history(mid or "1348660")
    print("form teams:", {k: len(v) for k, v in d["form"].items()})
    print("h2h:", len(d["h2h"]))
    print("current_odds:", d["current_odds"])
