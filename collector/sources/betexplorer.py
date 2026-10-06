"""
BetExplorer 接入 — 当前赔率 + 降赔榜（drift 信号）
https://www.betexplorer.com/

静态 HTML 可抓，免费，无需 key。注意联赛路径用 /football/（不是 /soccer/）。

覆盖: 各国各联赛（country/league slug）
用途:
- 联赛当前 1X2 赔率：get_league_odds()（与 Titan007 初盘对照做 drift）
- 降赔榜：get_dropping_odds()（赔率显著异动的场次，drift 信号输入）

说明: 单场详情页的完整赔率对比表是 JS 渲染的，静态抓不到；
开盘赔率已有 Titan007（90家欧指初盘），这里只取当前赔率+异动。
"""

import re
import time
from html.parser import HTMLParser

from _http import fetch_with_retry

BASE_URL = "https://www.betexplorer.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA}
POLITE_DELAY = 1

# 中文名 -> betexplorer 路径
LEAGUES = {
    "英超": "england/premier-league",
    "西甲": "spain/laliga",
    "意甲": "italy/serie-a",
    "德甲": "germany/bundesliga",
    "法甲": "france/ligue-1",
    "英冠": "england/championship",
    "西乙": "spain/laliga2",
    "德乙": "germany/2-bundesliga",
    "荷甲": "netherlands/eredivisie",
    "葡超": "portugal/liga-portugal",
    "苏超": "scotland/premiership",
    "美职": "usa/mls",
    "巴西甲": "brazil/serie-a-betano",
    "法乙": "france/ligue-2",
    "J1联赛": "japan/j1-league",
    "K1联赛": "south-korea/k-league-1",
}


def _get_html(url):
    time.sleep(POLITE_DELAY)
    raw = fetch_with_retry(url, headers=HEADERS, timeout=30, parse_json=False)
    return raw.decode("utf-8", "ignore")


class _MatchRowParser(HTMLParser):
    """解析联赛页比赛行：(home, away, score_or_None, [odd1, oddx, odd2], date)"""

    def __init__(self):
        super().__init__()
        self.in_row = False
        self.td_idx = -1
        self.buf = ""
        self.in_match_link = False
        self.teams = []
        self.cur = {}
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr" and not self.in_row:
            self.in_row = True
            self.td_idx = -1
            self.teams = []
            self.cur = {"home": None, "away": None, "score": None,
                        "odds": [], "date": None, "url": None}
        elif tag == "td" and self.in_row:
            self.td_idx += 1
            self.buf = ""
        elif tag == "a" and self.in_row and self.td_idx == 0:
            cls = attrs.get("class", "")
            if "in-match" in cls:
                self.in_match_link = True
                href = attrs.get("href", "")
                if href:
                    self.cur["url"] = href
        elif tag == "span" and self.in_match_link:
            self.buf = ""
            self._in_span = True
        elif tag == "strong" and self.in_match_link:
            self.buf = ""
            self._in_span = True
        # 赔率：td 或内层 span 上的 data-odd
        if tag in ("td", "span") and self.in_row and self.td_idx >= 2:
            odd = attrs.get("data-odd")
            if odd:
                try:
                    self.cur["odds"].append(float(odd))
                except ValueError:
                    pass

    def handle_endtag(self, tag):
        if tag in ("span", "strong") and getattr(self, "_in_span", False):
            t = self.buf.strip()
            if t and t != "-":
                self.teams.append(t)
            self._in_span = False
            self.buf = ""
        elif tag == "a" and self.in_match_link:
            self.in_match_link = False
        elif tag == "td" and self.in_row:
            txt = self.buf.strip()
            if self.td_idx == 1 and re.match(r"^\d+:\d+$", txt):
                self.cur["score"] = txt
            elif self.td_idx >= 4 and not self.cur["date"] and txt:
                # 日期列（最后一个非空文本列）
                if re.match(r"^[\d.]+$", txt):
                    self.cur["date"] = txt
            self.buf = ""
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if len(self.teams) >= 2:
                self.cur["home"] = self.teams[0]
                self.cur["away"] = self.teams[1]
                self.rows.append(self.cur)

    def handle_data(self, data):
        if self.in_row and getattr(self, "_in_span", False):
            self.buf += data
        elif self.in_row and self.td_idx in (1,) :
            self.buf += data


def get_league_odds(league_cn):
    """
    某联赛当前轮比赛行：比分（已赛）/ 赔率。
    返回: [{home, away, score, odd_1, odd_x, odd_2, date, url, league}]
    score 为 None 表示未开赛。
    """
    path = LEAGUES[league_cn]
    html = _get_html(f"{BASE_URL}/football/{path}/")
    p = _MatchRowParser()
    p.feed(html)
    out = []
    for r in p.rows:
        odds = r["odds"][:3]
        out.append({
            "home": r["home"],
            "away": r["away"],
            "score": r["score"],
            "odd_1": odds[0] if len(odds) > 0 else None,
            "odd_x": odds[1] if len(odds) > 1 else None,
            "odd_2": odds[2] if len(odds) > 2 else None,
            "date": r["date"],
            "url": r["url"],
            "league": league_cn,
            "source": "betexplorer",
        })
    return out


class _DroppingParser(HTMLParser):
    """解析降赔榜行"""

    def __init__(self):
        super().__init__()
        self.in_row = False
        self.td_idx = -1
        self.buf = ""
        self.cur = {}
        self.rows = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr" and not self.in_row:
            self.in_row = True
            self.td_idx = -1
            self.cur = {"match": None, "drop": None, "info": None, "odd": None}
        elif tag == "td" and self.in_row:
            self.td_idx += 1
            self.buf = ""

    def handle_endtag(self, tag):
        if tag == "td" and self.in_row:
            txt = self.buf.strip()
            if self.td_idx == 0:
                self.cur["match"] = txt or None
            elif self.td_idx == 1:
                m = re.search(r"(\d+)%", txt)
                self.cur["drop"] = int(m.group(1)) if m else None
            elif self.td_idx == 2:
                self.cur["info"] = txt or None
            self.buf = ""
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.cur["match"]:
                self.rows.append(self.cur)

    def handle_data(self, data):
        if self.in_row and self.td_idx in (0, 1, 2):
            self.buf += data


def get_dropping_odds():
    """
    降赔榜：赔率显著下降的场次。
    返回: [{match, drop_pct, info}]
    """
    html = _get_html(f"{BASE_URL}/football/dropping-odds/")
    p = _DroppingParser()
    p.feed(html)
    out = []
    for r in p.rows:
        # 过滤表头行
        if r["match"] and "Drop" not in r["match"]:
            out.append({
                "match": r["match"],
                "drop_pct": r["drop"],
                "info": r["info"],
                "source": "betexplorer",
            })
    return out


if __name__ == "__main__":
    print("=== BetExplorer 测试 ===")
    odds = get_league_odds("英超")
    print(f"联赛行: {len(odds)}")
    for o in odds[:5]:
        sc = o["score"] or "vs"
        print(f"  {o['home']} {sc} {o['away']}: "
              f"{o['odd_1']}/{o['odd_x']}/{o['odd_2']}")
    drop = get_dropping_odds()
    print(f"降赔榜: {len(drop)}")
    for d in drop[:5]:
        print(f"  {d['match']}: drop {d['drop_pct']}% ({d['info']})")
