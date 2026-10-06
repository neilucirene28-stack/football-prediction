"""
Transfermarkt 接入 — 伤停名单 + 俱乐部身价
https://www.transfermarkt.com/

静态 HTML 可抓，免费，无需 key。请求保持礼貌间隔。

覆盖: 各联赛（通过联赛 slug + 赛事 ID）
用途:
- 伤停/停赛名单（每日）：get_injuries()
- 俱乐部总身价（每周/每月）：get_club_values()，作为球队实力先验

说明: 曾评估 GitHub omkarcloud/transfermarkt-scraper（1★，
FastAPI 服务形态，需独立部署），与本项目"curl 直抓 + 每日脚本"
的形态不合，故采用直接静态解析，接口保持一致。
"""

import re
import time
from html.parser import HTMLParser

from _http import fetch_with_retry

BASE_URL = "https://www.transfermarkt.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 中文名 -> (transfermarkt slug, 赛事 ID)
LEAGUES = {
    "英超": ("premier-league", "GB1"),
    "西甲": ("laliga", "ES1"),
    "意甲": ("serie-a", "IT1"),
    "德甲": ("bundesliga", "L1"),
    "法甲": ("ligue-1", "FR1"),
    "英冠": ("championship", "GB2"),
    "西乙": ("laliga2", "ES2"),
    "德乙": ("2-bundesliga", "L2"),
    "荷甲": ("eredivisie", "NL1"),
    "葡超": ("liga-portugal", "PO1"),
    "苏超": ("scottish-premiership", "SC1"),
    "美职": ("major-league-soccer", "MLS1"),
    "巴西甲": ("campeonato-brasileiro-serie-a", "BRA1"),
    "阿职联": ("liga-profesional-de-futbol", "AR1N"),
    "法乙": ("ligue-2", "FR2"),
    "J1联赛": ("j1-league", "JAP1"),
    "K1联赛": ("k-league-1", "RSK1"),
}

HEADERS = {"User-Agent": UA}
# 礼貌间隔（秒）
POLITE_DELAY = 2

# Transfermarkt 位置名称（用于从嵌套表格中识别）
POSITIONS = {
    "Goalkeeper", "Centre-Back", "Left-Back", "Right-Back",
    "Defensive Midfield", "Central Midfield", "Attacking Midfield",
    "Left Midfield", "Right Midfield", "Left Winger", "Right Winger",
    "Centre-Forward", "Second Striker",
}


def _get_html(url):
    time.sleep(POLITE_DELAY)
    raw = fetch_with_retry(url, headers=HEADERS, timeout=30, parse_json=False)
    return raw.decode("utf-8", "ignore")


def parse_market_value(s):
    """
    '€24.28m' -> 24280000.0 ; '€13.16bn' -> 13160000000.0 ;
    '€500k' -> 500000.0 ; '-' / '' -> None
    纯函数，可离线测试。
    """
    if not s:
        return None
    s = s.strip().replace("€", "").replace(",", "")
    m = re.match(r"^([\d.]+)\s*(bn|m|k)?$", s, re.I)
    if not m:
        return None
    num, unit = float(m.group(1)), (m.group(2) or "").lower()
    mult = {"bn": 1e9, "m": 1e6, "k": 1e3}.get(unit, 1)
    return num * mult


class _ClubTableParser(HTMLParser):
    """解析联赛俱乐部表的 (club, total_value)"""

    def __init__(self):
        super().__init__()
        self.in_row = False
        self.in_hauptlink = False
        self.in_rechts = False
        self.cur_team = None
        self.cur_values = []
        self.buf = ""
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.in_row = True
            self.cur_team = None
            self.cur_values = []
        elif tag == "td" and self.in_row:
            cls = attrs.get("class", "")
            if "hauptlink" in cls:
                self.in_hauptlink = True
                self.buf = ""
            elif "rechts" in cls:
                self.in_rechts = True
                self.buf = ""

    def handle_endtag(self, tag):
        if tag == "td":
            if self.in_hauptlink:
                # hauptlink 里第一个 a 的文本即队名
                self.in_hauptlink = False
            elif self.in_rechts:
                self.cur_values.append(self.buf.strip())
                self.in_rechts = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.cur_team and self.cur_values:
                # 排除排名数字等误抓（纯数字不是队名）
                if not self.cur_team.strip().isdigit():
                    # 最后一个 rechts 列为俱乐部总身价
                    self.rows.append((self.cur_team, self.cur_values[-1]))
            self.cur_team = None

    def handle_data(self, data):
        if self.in_hauptlink and self.cur_team is None:
            t = data.strip()
            if t:
                self.cur_team = t
        elif self.in_rechts:
            self.buf += data


def get_club_values(league_cn):
    """
    某联赛各俱乐部总身价。
    返回: [{club, market_value_eur, league}]
    """
    slug, comp_id = LEAGUES[league_cn]
    url = f"{BASE_URL}/{slug}/startseite/wettbewerb/{comp_id}"
    html = _get_html(url)
    p = _ClubTableParser()
    p.feed(html)
    out = []
    for team, val_raw in p.rows:
        out.append({
            "club": team,
            "market_value_eur": parse_market_value(val_raw),
            "market_value_raw": val_raw,
            "league": league_cn,
            "source": "transfermarkt",
        })
    return out


class _InjuryTableParser(HTMLParser):
    """
    解析联赛伤停表。表头: Player/Position | Club | Injury | until | Market Value。
    球员行第一格是嵌套 inline-table，用球员链接定位行，避免嵌套 tr 干扰。
    """

    def __init__(self):
        super().__init__()
        self.in_items_table = False  # 只在 <table class="items"> 内解析
        self.table_depth = 0
        self.in_main_tr = False   # 顶层球员行（class=odd/even）
        self.td_idx = -1
        self.buf = ""
        self.cur = {}
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table":
            self.table_depth += 1
            if "items" in attrs.get("class", ""):
                self.in_items_table = True
        if not self.in_items_table:
            return
        if tag == "tr" and self.table_depth == 1:
            cls = attrs.get("class", "")
            if cls in ("odd", "even"):
                self.in_main_tr = True
                self.td_idx = -1
                self.cur = {"player": None, "position": None, "club": None,
                            "injury": None, "return": None}
        elif tag == "td" and self.in_main_tr and self.table_depth == 1:
            self.td_idx += 1
            self.buf = ""
        elif tag == "a" and self.in_main_tr:
            href = attrs.get("href", "")
            # 球员链接含 /profil/spieler/（在嵌套 inline-table 内，depth=2）
            if "/profil/spieler/" in href and attrs.get("title") \
                    and not self.cur["player"]:
                self.cur["player"] = attrs["title"]
        elif tag == "img" and self.in_main_tr:
            # 俱乐部徽章 class 含 tiny_wappen；球员头像是 bilderrahmen-fixed
            cls = attrs.get("class", "")
            if "tiny_wappen" in cls and attrs.get("alt") \
                    and not self.cur["club"]:
                self.cur["club"] = attrs["alt"]

    def handle_endtag(self, tag):
        if tag == "table":
            self.table_depth = max(0, self.table_depth - 1)
            if self.table_depth == 0:
                self.in_items_table = False
                self.in_main_tr = False
            return
        if not self.in_items_table:
            return
        if tag == "td" and self.in_main_tr and self.table_depth == 1:
            txt = self.buf.strip()
            if self.td_idx == 0:
                if self.cur["player"] and txt.startswith(self.cur["player"]):
                    txt = txt[len(self.cur["player"]):].strip()
                self.cur["position"] = txt or None
            elif self.td_idx == 2:
                self.cur["injury"] = txt or None
            elif self.td_idx == 3:
                self.cur["return"] = txt or None
            self.buf = ""
        elif tag == "tr" and self.in_main_tr and self.table_depth == 1:
            self.in_main_tr = False
            if self.cur.get("player"):
                self.rows.append(self.cur)

    def handle_data(self, data):
        if not (self.in_items_table and self.in_main_tr):
            return
        if self.table_depth == 1 and self.td_idx in (0, 2, 3):
            self.buf += data
        elif self.table_depth == 2 and not self.cur.get("position"):
            # 位置在嵌套 inline-table 内，匹配固定名单
            t = data.strip()
            if t in POSITIONS:
                self.cur["position"] = t


def get_injuries(league_cn):
    """
    某联赛伤停/停赛名单。
    返回: [{player, position, club, injury, return_date, league}]
    """
    slug, comp_id = LEAGUES[league_cn]
    url = f"{BASE_URL}/{slug}/verletzteSpieler/wettbewerb/{comp_id}"
    html = _get_html(url)
    p = _InjuryTableParser()
    p.feed(html)
    out = []
    for r in p.rows:
        out.append({
            "player": r["player"],
            "position": r["position"],
            "club": r["club"],
            "injury": r["injury"],
            "return_date": r["return"],
            "league": league_cn,
            "source": "transfermarkt",
        })
    return out


if __name__ == "__main__":
    print("=== Transfermarkt 测试 (英超) ===")
    clubs = get_club_values("英超")
    print(f"俱乐部: {len(clubs)}")
    for c in clubs[:3]:
        v = c["market_value_eur"]
        print(f"  {c['club']}: €{v/1e6:.0f}m" if v else f"  {c['club']}: ?")
    inj = get_injuries("英超")
    print(f"伤停: {len(inj)}人")
    for i in inj[:5]:
        print(f"  {i['player']} ({i['club']}): {i['injury']}")
