"""
FootyStats 接入 — 球队 xG 等独立进攻/防守信号
https://footystats.org/

静态 HTML 可抓，免费，无需 key。

覆盖: 各国各联赛（country/league slug）
用途: 球队 xG/xGA（独立于自有引擎的第三方 xG 信号），
      用于总进球/上下单双玩法的独立校验。

说明: 联赛 xG 页 (/country/league/xg) 的球队主行含
      MP/xG/xGA/xGD/GF/GA，用 HTMLParser 解析。
"""

import time
from html.parser import HTMLParser

from _http import fetch_with_retry

BASE_URL = "https://footystats.org"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
POLITE_DELAY = 1

# 中文名 -> footystats 路径
LEAGUES = {
    "英超": "england/premier-league",
    "西甲": "spain/la-liga",
    "意甲": "italy/serie-a",
    "德甲": "germany/bundesliga",
    "法甲": "france/ligue-1",
    "英冠": "england/championship",
    "荷甲": "netherlands/eredivisie",
    "葡超": "portugal/liga-nos",
    "苏超": "scotland/premiership",
    "美职": "usa/mls",
    "巴西甲": "brazil/serie-a",
    "巴西乙": "brazil/serie-b",
    "西乙": "spain/segunda-division",
    "德乙": "germany/2-bundesliga",
    "法乙": "france/ligue-2",
    "J1联赛": "japan/j1-league",
    "K1联赛": "south-korea/k-league-1",
}


def _get_html(url):
    time.sleep(POLITE_DELAY)
    raw = fetch_with_retry(url, headers=HEADERS, timeout=30, parse_json=False)
    return raw.decode("utf-8", "ignore")


class _XgTableParser(HTMLParser):
    """
    解析 xG 页球队主行。
    主行特征：含 /clubs/ 链接；10 个顶层 td：
    [#, crest, Team, MP, xG, xGA, xGD, GF, GA, xGvsActual]
    子行（Overall/Home/Away）跳过：它们不含 clubs 链接。
    """

    def __init__(self):
        super().__init__()
        self.table_depth = 0
        self.in_row = False
        self.row_has_club = False
        self.in_td = False
        self.buf = ""
        self.cells = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.table_depth += 1
        if tag == "tr" and self.table_depth == 1 and not self.in_row:
            self.in_row = True
            self.row_has_club = False
            self.in_td = False
            self.buf = ""
            self.cells = []
        elif tag == "td" and self.in_row and self.table_depth == 1:
            # 新 td 开始：若上一个未闭合，先结算（页面部分 td 未闭合）
            if self.in_td:
                self.cells.append(self.buf.strip())
            self.in_td = True
            self.buf = ""
        elif tag == "a" and self.in_row:
            href = attrs.get("href", "")
            if "/clubs/" in href:
                self.row_has_club = True

    def handle_endtag(self, tag):
        if tag == "table":
            self.table_depth = max(0, self.table_depth - 1)
        if tag == "td" and self.in_row and self.in_td and self.table_depth == 1:
            self.cells.append(self.buf.strip())
            self.in_td = False
            self.buf = ""
        elif tag == "tr" and self.in_row and self.table_depth == 1:
            if self.in_td:
                self.cells.append(self.buf.strip())
                self.in_td = False
            self.in_row = False
            if self.row_has_club and len(self.cells) >= 10:
                self.rows.append(self.cells)

    def handle_data(self, data):
        if self.in_row and self.in_td:
            self.buf += data


def _clean_team_name(s):
    """队名在单元格内重复出现（链接文本+悬浮窗），取最短重复前缀。"""
    s = s.strip()
    n = len(s)
    for k in range(3, n // 2 + 1):
        if s[:k] == s[k:2 * k]:
            return s[:k].strip()
    return s


def _to_float(s):
    try:
        return float(s.replace("+", "").strip())
    except (ValueError, AttributeError):
        return None


def _to_int(s):
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return None


def get_team_xg(league_cn):
    """
    某联赛各球队 xG 数据。
    返回: [{team, mp, xg, xga, xgd, gf, ga, league}]
    """
    path = LEAGUES[league_cn]
    html = _get_html(f"{BASE_URL}/{path}/xg")
    p = _XgTableParser()
    p.feed(html)
    out = []
    for c in p.rows:
        # c: [#, crest, Team, MP, xG, xGA, xGD, GF, GA, xGvsActual]
        team = _clean_team_name(c[2])
        out.append({
            "team": team,
            "mp": _to_int(c[3]),
            "xg": _to_float(c[4]),
            "xga": _to_float(c[5]),
            "xgd": _to_float(c[6]),
            "gf": _to_float(c[7]),
            "ga": _to_float(c[8]),
            "league": league_cn,
            "source": "footystats",
        })
    return out


if __name__ == "__main__":
    print("=== FootyStats xG 测试 (英超) ===")
    teams = get_team_xg("英超")
    print(f"球队: {len(teams)}")
    for t in teams[:5]:
        print(f"  {t['team']}: MP={t['mp']} xG={t['xg']} xGA={t['xga']} xGD={t['xgd']}")
