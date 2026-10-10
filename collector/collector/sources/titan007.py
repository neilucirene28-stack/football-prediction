"""Titan007 数据源（https://live.titan007.com/oldIndexall.aspx）。

只采集公开免费模块，明确不碰会员模块：
    黑名单（绝不请求、不解析、不推断）：情报、AI解读、会员、HOT 方案。
免费模块无数据时如实留空，不从会员内容补齐。

数据流（全部为服务端渲染的静态 HTML/JS，无需浏览器）：
  1. 列表：/vbsxml/Ballpub/BaSID.js  ->  matchid 列表（Ba_Soccer="..."）
  2. 每场：live.titan007.com/detail/{id}cn.htm -> state / 开球时间 / 赛事 / 队名
  3. 详情（公开）：
     - zq.titan007.com/analysis/{id}cn.htm   分析：对赛往绩/近期战绩/相同盘路/赛前简报
     - 1x2d.titan007.com/{id}.js             胜平负：百家欧指初盘/即时 + 球队/中立场/天气
     - vip.titan007.com/AsianOdds_n.aspx     亚让：各公司初盘/即时
     - vip.titan007.com/OverDown_n.aspx      进球数：各公司初盘/即时
     - vip.titan007.com/Corner.aspx          角球：有则解析，无则留空
  4. 指数走势（公开 changeDetail 指数走势页 + 内嵌 chartFlash 走势图）：
     - 欧指变动曲线：1x2d.js 的 gameDetail 本就是每家公司赔率变动关键点
       （零额外请求，x12_movement）
     - 亚让/大小球变动曲线：changeDetail/handicap.aspx、overunder.aspx
       -> chartFlash.aspx 内嵌 dataStr（每家公司 2 次请求，opt-in）

盘口口径：负数 = 主队让球（与引擎一致）。
"""
from __future__ import annotations

import html as htmlmod
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from .base import Source

# ---------------------------------------------------------------------------
# 会员模块黑名单：这些 URL 永远不请求
# ---------------------------------------------------------------------------
MEMBER_ONLY_MODULES = ("情报", "AI解读", "会员", "HOT 方案")

ENTRY_URL = "https://live.titan007.com/oldIndexall.aspx"
_BASID_URL = "https://live.titan007.com/vbsxml/Ballpub/BaSID.js?r=007"
_DETAIL_URL = "https://live.titan007.com/detail/{mid}cn.htm"
_ANALYSIS_URL = "https://zq.titan007.com/analysis/{mid}cn.htm"
_X12JS_URL = "https://1x2d.titan007.com/{mid}.js?r=007"

# 分析页战绩保留深度（条/队）：h_data/a_data/v_data 按实际行数全解析，
# 上限 HISTORY_DEPTH。页面展示为几十场/队（用户 bf 录屏确认）。
HISTORY_DEPTH = 30
_ASIAN_URL = "https://vip.titan007.com/AsianOdds_n.aspx?id={mid}&l=0"
_OU_URL = "https://vip.titan007.com/OverDown_n.aspx?id={mid}&l=0"
_CORNER_URL = "https://vip.titan007.com/Corner.aspx?id={mid}&l=0"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_TZ8 = timezone(timedelta(hours=8))

# 亚盘盘口文字 -> 让球数（正数表示盘口大小，调用方按"主让为负"取号）
_AH_TEXT = {
    "平手": 0.0, "平手/半球": 0.25, "半球": 0.5, "半球/一球": 0.75,
    "一球": 1.0, "一球/球半": 1.25, "球半": 1.5, "球半/两球": 1.75,
    "两球": 2.0, "两球/两球半": 2.25, "两球半": 2.5, "两球半/三球": 2.75,
    "三球": 3.0, "三球/三球半": 3.25, "三球半": 3.5,
}
# 007 状态码：0 未开赛；1-5 进行中；-1 完场；其它为取消/推迟/中断
_STATE_MAP = {0: "scheduled", 1: "live", 2: "live", 3: "live",
              4: "live", 5: "live", -1: "finished"}

# 欧指汇总优先参考的公司（game[] 中的英文名）
_KEY_X12 = ("Lottery Official", "Bet 365", "Macauslot")


def _curl_get(url: str, referer: str, timeout: int = 25) -> bytes:
    """urllib 被断连时的传输层兜底：curl 走同一代理，返回原始字节。"""
    import subprocess
    out = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout), "--compressed",
         "-H", f"User-Agent: {_UA}", "-H", f"Referer: {referer}",
         "-H", "Accept: */*", url],
        capture_output=True, timeout=timeout + 10)
    if out.returncode != 0 or not out.stdout:
        raise ConnectionError(
            f"curl fallback failed rc={out.returncode}: "
            f"{out.stderr.decode('utf-8', 'replace')[:200]}")
    return out.stdout


def _http_get(url: str, referer: str, timeout: int = 25) -> str:
    import http.client

    req = urllib.request.Request(url, headers={
        "User-Agent": _UA, "Referer": referer, "Accept": "*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except http.client.IncompleteRead as e:
        # 某些 007 静态服务器 Content-Length 不准，用已读到的部分数据
        raw = e.partial
        if not raw:
            raise
    except Exception:
        # urllib TLS 指纹偶发被源站直接断连（如 zq.titan007.com），
        # 用 curl（经同一代理）重试一次；仍失败则抛出。
        raw = _curl_get(url, referer, timeout)
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _strip_tags(s: str) -> str:
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _num(text: str):
    try:
        v = float(str(text).strip().replace(",", ""))
        return v if v == v else None
    except (ValueError, TypeError):
        return None


def parse_ah_number(text: str):
    """亚盘盘口文字 -> 引擎口径（负数=主队让球）。无法解析返回 None。"""
    t = (text or "").strip()
    if not t:
        return None
    recv = t.startswith("受让")
    if recv:
        t = t[2:]
    base = _AH_TEXT.get(t)
    if base is None:
        return None
    return base if recv else -base


def parse_ou_number(text: str):
    """大小球盘口文字 -> 数字，如 '2.5/3' -> 2.75。"""
    t = (text or "").strip()
    if not t:
        return None
    if "/" in t:
        parts = [_num(p) for p in t.split("/")]
        if all(p is not None for p in parts):
            return sum(parts) / len(parts)
        return None
    return _num(t)


def _parse_js_string_array(js: str, varname: str) -> list[str]:
    """解析 var X=Array("a","b",...) 中的字符串元素。"""
    i = js.find(f"var {varname}=Array(")
    if i < 0:
        return []
    pos = i + len(f"var {varname}=Array(")
    elems, n = [], len(js)
    while pos < n:
        while pos < n and js[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or js[pos] == ")":
            break
        if js[pos] != '"':
            break
        pos += 1
        buf = []
        while pos < n and js[pos] != '"':
            if js[pos] == "\\" and pos + 1 < n:
                buf.append(js[pos + 1])
                pos += 2
            else:
                buf.append(js[pos])
                pos += 1
        pos += 1
        elems.append("".join(buf))
    return elems


def _parse_js_row_array(html: str, varname: str) -> list[list]:
    """解析 var v_data=[[...],[...]] 这类嵌套数组（元素含 HTML/转义引号）。"""
    m = re.search(rf"{re.escape(varname)}\s*=\s*\[", html)
    if not m:
        return []
    pos = m.end() - 1
    rows, n = [], len(html)
    assert html[pos] == "["
    pos += 1
    while pos < n:
        while pos < n and html[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or html[pos] == "]":
            break
        if html[pos] != "[":
            break
        pos += 1
        row = []
        while pos < n and html[pos] != "]":
            while pos < n and html[pos] in " \t\r\n,":
                pos += 1
            if pos < n and html[pos] == "]":
                break
            if html[pos] in ("'", '"'):
                q = html[pos]
                pos += 1
                buf = []
                while pos < n and html[pos] != q:
                    if html[pos] == "\\" and pos + 1 < n:
                        buf.append(html[pos + 1])
                        pos += 2
                    else:
                        buf.append(html[pos])
                        pos += 1
                pos += 1
                row.append("".join(buf))
            else:
                m = re.match(r"-?[\d.]+", html[pos:])
                if m:
                    row.append(float(m.group(0)))
                    pos += m.end()
                else:
                    m2 = re.match(r"[^,\]]+", html[pos:])
                    row.append(m2.group(0).strip() if m2 else "")
                    pos += m2.end() if m2 else 1
        pos += 1
        rows.append(row)
    return rows


class Titan007Source(Source):
    name = "titan007"

    def __init__(self, entry_url: str = ENTRY_URL, max_matches: int = 15,
                 days_ahead: int = 7, request_delay: float = 0.3,
                 timeout: int = 25):
        self.entry_url = entry_url or ENTRY_URL
        self.max_matches = int(os.getenv("TITAN_MAX_MATCHES", max_matches))
        self.days_ahead = int(os.getenv("TITAN_DAYS_AHEAD", days_ahead))
        self.delay = float(os.getenv("TITAN_REQUEST_DELAY", request_delay))
        self.timeout = timeout

    # -- HTTP ------------------------------------------------------------
    def _get(self, url: str) -> str:
        time.sleep(self.delay)
        return _http_get(url, referer=self.entry_url, timeout=self.timeout)

    def fetch_company_trend(self, mid: str, company_id: str,
                            market: str) -> dict | None:
        """抓取单家公司单市场指数走势（公开 changeDetail 页）。

        market: 'asian' | 'ou'。两步：走势 tab 页 -> 提取 chartFlash iframe ->
        解析内嵌 dataStr。任一步失败/无数据返回 None（诚实缺失）。
        注意：每家公司 2 次请求，批量抓取成本高，默认仅在 with_trends=True 时用。
        """
        page = self._TREND_PAGES.get(market)
        if not page:
            return None
        try:
            tab = self._get(
                f"https://vip.titan007.com/changeDetail/{page}.aspx"
                f"?id={mid}&companyid={company_id}&l=0")
            m = re.search(r'chartFlash\.aspx\?[^"\']+', tab)
            if not m:
                return None
            # iframe src 里的 company=中文公司名：urllib 发非 ASCII 会抛错，
            # 而服务端对 percent-encoded 值返回空；实测 company 仅展示用，
            # 置 ASCII 占位后数据一致。
            chart_qs = re.sub(r"([?&])company=[^&]*", r"\1company=x",
                              htmlmod.unescape(m.group(0)))
            chart = self._get("https://vip.titan007.com/changeDetail/"
                              + chart_qs)
            d = re.search(r"var dataStr = '(.*?)';", chart, re.S)
            if not d:
                return None
            pts = self.parse_trend_datastr(d.group(1), market)
            if not pts:
                return None
            return {"market": market, "company_id": company_id,
                    "n": len(pts), "points": pts,
                    "first_t": pts[0]["t"], "last_t": pts[-1]["t"],
                    "granularity": "key_change_points",
                    "captured_at": datetime.now(timezone.utc).isoformat()}
        except Exception:
            return None

    # -- 列表 ------------------------------------------------------------
    def _list_match_ids(self) -> list[str]:
        # 代理偶发截断（IncompleteRead），重试几次
        js = ""
        for _ in range(4):
            try:
                js = self._get(_BASID_URL)
                if 'var Ba_Soccer="' in js:
                    break
            except Exception:
                time.sleep(2)
        m = re.search(r'var Ba_Soccer="([\d,]+)"', js)
        if not m:
            return []
        return [x for x in m.group(1).split(",") if x]

    @staticmethod
    def parse_detail(html: str, mid: str) -> dict | None:
        """解析 detail 页：state / 开球时间 / 赛事 / 队名。"""
        m_state = re.search(r"var state\s*=\s*(-?\d+)", html)
        m_time = re.search(r"var strTime\s*=\s*'([\d\- :]+)'", html)
        m_title = re.search(r"<title>(.+?)\s+VS\s+(.+?)\(", html)
        m_league = re.search(r'class="LName"[^>]*>([^<]+)</a>', html)
        if not (m_state and m_time and m_title):
            return None
        state = int(m_state.group(1))
        try:
            kickoff = datetime.strptime(m_time.group(1).strip(),
                                        "%Y-%m-%d %H:%M").replace(tzinfo=_TZ8)
        except ValueError:
            return None
        return {
            "matchid": mid,
            "status": _STATE_MAP.get(state, "postponed"),
            "kickoff_at": kickoff.isoformat(),
            "home_team": _strip_tags(m_title.group(1)),
            "away_team": _strip_tags(m_title.group(2)),
            "competition": _strip_tags(m_league.group(1)) if m_league else "",
        }

    def _upcoming_ids(self) -> list[dict]:
        now = datetime.now(_TZ8)
        out = []
        for mid in self._list_match_ids():
            try:
                info = self.parse_detail(self._get(_DETAIL_URL.format(mid=mid)), mid)
            except Exception:
                continue
            if not info or info["status"] != "scheduled":
                continue
            try:
                ko = datetime.fromisoformat(info["kickoff_at"])
            except ValueError:
                continue
            if timedelta(minutes=-15) <= ko - now <= timedelta(days=self.days_ahead):
                out.append(info)
            if len(out) >= self.max_matches * 2:
                break
        out.sort(key=lambda d: d["kickoff_at"])
        return out[:self.max_matches]

    # -- 详情解析 ---------------------------------------------------------
    @staticmethod
    def _history_rows(rows: list[list], team_id: int,
                      perspective: str = "team") -> list[dict]:
        """近期战绩行 -> [{gf,ga,venue,date,comp}]，最新在前。"""
        out = []
        for r in rows:
            try:
                if len(r) < 16:
                    continue
                date_s = str(r[0]).strip()
                try:
                    yy, mm, dd = date_s.split("-")
                    date = f"20{yy}-{mm}-{dd}"
                except ValueError:
                    date = date_s
                hid, aid = int(r[4]), int(r[6])
                hname = _strip_tags(str(r[5]))
                aname = _strip_tags(str(r[7]))
                hg, ag = int(r[8]), int(r[9])
                neutral = "(中)" in hname or "(中)" in aname
                if hid == team_id:
                    gf, ga = hg, ag
                    venue = "N" if neutral else "H"
                    opp = aname.replace("(中)", "")
                elif aid == team_id:
                    gf, ga = ag, hg
                    venue = "N" if neutral else "A"
                    opp = hname.replace("(中)", "")
                else:
                    continue
                entry = {"gf": gf, "ga": ga, "venue": venue,
                         "date": date, "comp": str(r[2]).strip(),
                         "opponent": opp.strip()}
                if perspective == "h2h":
                    entry = {"gf": gf, "ga": ga, "date": date,
                             "comp": str(r[2]).strip()}
                out.append(entry)
            except (ValueError, TypeError, IndexError):
                continue
        return out

    @staticmethod
    def parse_analysis(html: str, home_id: int, away_id: int) -> dict:
        """解析分析页：对赛往绩 / 近期战绩 / 相同盘路摘要。

        深度: h_data/a_data/v_data 数组按页面实际行数全解析，最多保留
        HISTORY_DEPTH 条/队（页面通常带 30 场，用户在 bf 录屏中确认过
        几十场/队的展示；zq.titan007.com 当前对机房 IP 断连，行数上限
        未在现网逐场核验，解析器按实际行数兜底）。
        """
        detail: dict = {}
        v_rows = _parse_js_row_array(html, "v_data")
        h_rows = _parse_js_row_array(html, "h_data")
        a_rows = _parse_js_row_array(html, "a_data")
        h2h = Titan007Source._history_rows(v_rows, home_id, perspective="h2h")
        home_recent = Titan007Source._history_rows(h_rows, home_id)[:HISTORY_DEPTH]
        away_recent = Titan007Source._history_rows(a_rows, away_id)[:HISTORY_DEPTH]
        if h2h:
            detail["h2h"] = h2h[:HISTORY_DEPTH]
        if home_recent:
            detail["home_recent"] = home_recent
        if away_recent:
            detail["away_recent"] = away_recent
        # 相同盘路摘要（纯文本摘要，不做结构化推断）
        m = re.search(r"相同盘路(.{0,600})", html, re.S)
        if m:
            txt = _strip_tags(m.group(1))
            if txt:
                detail.setdefault("raw_extra", {})["same_handicap_text"] = txt[:400]
        return detail

    @staticmethod
    def parse_x12js(js: str) -> dict:
        """解析 1x2d 数据 JS：球队/中立场/时间 + 百家欧指。"""
        out: dict = {}
        for var in ("hometeam_cn", "guestteam_cn", "matchname_cn",
                    "temperature", "neutrality", "MatchTime"):
            m = re.search(rf'var {var}="([^"]*)"', js)
            if m:
                out[var] = m.group(1)
        m = re.search(r"var hometeamID=(\d+)", js)
        if m:
            out["hometeamID"] = int(m.group(1))
        m = re.search(r"var guestteamID=(\d+)", js)
        if m:
            out["guestteamID"] = int(m.group(1))
        companies = []
        for elem in _parse_js_string_array(js, "game"):
            parts = elem.split("|")
            if len(parts) < 21:
                continue
            try:
                companies.append({
                    "id": parts[0], "rid": parts[1], "name": parts[2],
                    "open": [_num(parts[3]), _num(parts[4]), _num(parts[5])],
                    "open_return": _num(parts[9]),
                    "live": [_num(parts[10]), _num(parts[11]), _num(parts[12])],
                    "live_return": _num(parts[16]),
                    "kelly": [_num(parts[17]), _num(parts[18]), _num(parts[19])],
                    "short": parts[21] if len(parts) > 21 else "",
                })
            except (ValueError, IndexError):
                continue
        out["companies"] = companies
        rid2name = {c["rid"]: c["name"] for c in companies if c.get("rid")}
        history = {}
        for elem in _parse_js_string_array(js, "gameDetail"):
            if "^" not in elem:
                continue
            cid, _, series = elem.partition("^")
            snaps = []
            for snap in series.split(";"):
                p = snap.split("|")
                if len(p) >= 4 and _num(p[0]):
                    snaps.append({"home": _num(p[0]), "draw": _num(p[1]),
                                  "away": _num(p[2]), "time": p[3]})
            if snaps:
                history[cid] = snaps
        out["history"] = history
        # 指数走势（欧指变动曲线）：gameDetail 本就是每家公司的赔率变动
        # 关键点序列（每个快照=一次赔率变动），源数据为倒序（最新在前），
        # 此处转为时间正序并按公司名 key，便于直接服务 drift 特征。
        # 数据粒度：关键变动点（非等间隔采样），时间戳只有"MM-DD HH:MM"。
        movement = {}
        for cid, snaps in history.items():
            name = rid2name.get(cid)
            if not name:
                continue
            pts = list(reversed(snaps))
            movement[name] = {"n": len(pts), "points": pts,
                             "first_t": pts[0]["time"],
                             "last_t": pts[-1]["time"]}
        out["movement"] = movement
        return out

    # -- 指数走势（亚指/大小球 changeDetail 公开页） -------------------------
    _TREND_PAGES = {"asian": "handicap", "ou": "overunder"}

    @staticmethod
    def parse_trend_datastr(datastr: str, market: str) -> list[dict] | None:
        """解析走势图页内嵌的 dataStr。
        格式：'MM-DD HH:MM^v1^v2^v3^v4,...'，每个点=一次指数变动（关键变动点，
        非等间隔采样）。asian: t^主水^主让^客让^客水；ou: t^大球水^盘口^盘口^小球水。
        找不到/格式非法返回 None（诚实缺失，不猜）。"""
        if not datastr or "^" not in datastr:
            return None
        pts = []
        for chunk in datastr.split(","):
            p = chunk.split("^")
            if len(p) != 5 or not p[0]:
                continue
            v = [_num(x) for x in p[1:]]
            if any(x is None for x in v):
                continue
            if market == "asian":
                pts.append({"t": p[0], "home_water": v[0],
                            "handicap": v[1], "away_water": v[3]})
            elif market == "ou":
                pts.append({"t": p[0], "over_water": v[0],
                            "line": v[1], "under_water": v[3]})
            else:
                return None
        return pts or None

    @staticmethod
    def trend_company_ids(html: str) -> list[tuple]:
        """从亚让/大小球对比页提取 [(companyID, 公司名)]（去重保序）。
        companyID 用于 changeDetail 走势页，名字是页面显示名（带*掩码）。"""
        pairs = []
        for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            row = m.group(1)
            cid = re.search(r"companyID=['\"]?(\d+)", row)
            nm = re.search(r'<td height="25">(.*?)</td>', row, re.S)
            if cid and nm:
                name = _strip_tags(nm.group(1))
                if (name and "公司" not in name
                        and not any(p[0] == cid.group(1) for p in pairs)):
                    pairs.append((cid.group(1), name))
        return pairs

    @staticmethod
    def _parse_odds_table(html: str, kind: str) -> list[dict]:
        """解析亚让/大小球/角球对比表行。kind: asian|ou|corner。

        真实行结构（亚让/大小球）：
          <td><input checkbox></td><td height="25">公司</td>
          <td><span 趋势></span></td>
          <td>初水1</td><td>初盘</td><td>初水2</td>
          <td>即水1</td><td>即盘</td><td>即水2</td> ...
        角球表没有 checkbox 列和趋势列。
        """
        rows = []
        pat = re.compile(
            r'<td height="25">([^<>]{1,30})</td>\s*'
            r"(?:<td[^>]*>\s*<span[^>]*>.*?</span>\s*</td>\s*)?"
            r"<td[^>]*>([^<>]*)</td>\s*<td[^>]*>([^<>]*)</td>\s*"
            r"<td[^>]*>([^<>]*)</td>\s*<td[^>]*>([^<>]*)</td>\s*"
            r"<td[^>]*>([^<>]*)</td>\s*<td[^>]*>([^<>]*)</td>",
            re.S)
        for m in pat.finditer(html):
            name = _strip_tags(m.group(1))
            if not name or "公司" in name or len(name) > 20:
                continue
            cells = [_strip_tags(m.group(i)) for i in range(2, 8)]
            if not any(cells):
                continue
            if any(r["company"] == name for r in rows):
                continue  # 多盘公司只保留第一行
            rows.append({"company": name,
                         "open_water1": cells[0], "open_line": cells[1],
                         "open_water2": cells[2],
                         "live_water1": cells[3], "live_line": cells[4],
                         "live_water2": cells[5]})
        return rows

    # -- 组装 ------------------------------------------------------------
    @staticmethod
    def _avg_x12(companies: list[dict], key: str):
        vals = {"home": [], "draw": [], "away": []}
        for c in companies:
            o = c.get(key) or []
            if len(o) == 3 and all(v and v > 1 for v in o):
                vals["home"].append(o[0])
                vals["draw"].append(o[1])
                vals["away"].append(o[2])
        if not vals["home"]:
            return None
        return {k: round(sum(v) / len(v), 3) for k, v in vals.items()}

    def _build_match(self, info: dict, with_trends: bool = False) -> dict | None:
        mid = info["matchid"]
        match = {
            "external_id": f"titan-{mid}",
            "_source": "titan007",
            "competition": info.get("competition", ""),
            "home_team": info["home_team"],
            "away_team": info["away_team"],
            "kickoff_at": info["kickoff_at"],
            "status": info.get("status", "scheduled"),
            "snapshot_at": datetime.now(timezone.utc).isoformat(),
            "raw": {"matchid": mid},
        }
        match["raw"]["collection_started_at"] = match["snapshot_at"]
        # -- 1x2 数据 JS：球队 / 中立场 / 欧指 --
        try:
            x = self.parse_x12js(self._get(_X12JS_URL.format(mid=mid)))
        except Exception:
            x = {}
        home_id = x.get("hometeamID")
        away_id = x.get("guestteamID")
        if x.get("hometeam_cn"):
            match["home_team"] = x["hometeam_cn"]
        if x.get("guestteam_cn"):
            match["away_team"] = x["guestteam_cn"]
        if x.get("matchname_cn"):
            match["competition"] = x["matchname_cn"]
        if x.get("neutrality") == "1":
            match["neutral_site"] = True
        if x.get("MatchTime"):
            try:
                yy, mo, dd, hh, mi, _ss = x["MatchTime"].split(",")
                mo = int(mo) + 1  # 月份从 0 开始
                match["kickoff_at"] = datetime(
                    int(yy), mo, int(dd), int(hh), int(mi),
                    tzinfo=_TZ8).isoformat()
            except (ValueError, TypeError):
                pass
        companies = x.get("companies", [])
        if companies:
            avg_live = self._avg_x12(companies, "live")
            avg_open = self._avg_x12(companies, "open")
            if avg_live:
                match["odds"] = avg_live
            if avg_open:
                match["opening_odds"] = avg_open
            match["raw"]["x12_companies"] = {
                c["name"]: {"open": c["open"], "live": c["live"],
                            "live_return": c["live_return"],
                            "kelly": c["kelly"]}
                for c in companies}
            # 初盘快照（资金流向 MVP）：每家公司初盘 + 首次抓取时间戳。
            # 初盘是庄家自己的开盘价（静态），snapshot 覆盖率 = 漂移特征生死线。
            opens = {c["name"]: c["open"] for c in companies
                     if c.get("open") and len(c["open"]) == 3
                     and all(v and v > 1 for v in c["open"])}
            match["raw"]["x12_open_snapshot"] = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "n_companies": len(opens),
                "companies": opens,
            }
            key_co = {c["name"]: c for c in companies}
            match["raw"]["x12_key"] = {
                k: {"open": key_co[k]["open"], "live": key_co[k]["live"]}
                for k in _KEY_X12 if k in key_co}
            if x.get("history"):
                match["raw"]["x12_history_n"] = {k: len(v)
                                                 for k, v in x["history"].items()}
            # 指数走势（欧指变动曲线）：gameDetail 已含每家公司赔率变动关键点，
            # 零额外请求。直接服务 drift 特征（初盘->即时方向/幅度验证）。
            if x.get("movement"):
                mv = x["movement"]
                match["raw"]["x12_movement"] = mv
                match["raw"]["x12_movement_meta"] = {
                    "granularity": "key_change_points",
                    "n_companies": len(mv),
                    "median_n_points": sorted(
                        len(v["points"]) for v in mv.values()
                    )[len(mv) // 2] if mv else 0,
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                }
        if x.get("temperature"):
            match["raw"]["temperature"] = x["temperature"]

        # -- 分析页：往绩 --
        try:
            ana = self.parse_analysis(self._get(_ANALYSIS_URL.format(mid=mid)),
                                      home_id or 0, away_id or 0)
            for k in ("h2h", "home_recent", "away_recent"):
                if ana.get(k):
                    match[k] = ana[k]
            if ana.get("raw_extra"):
                match["raw"].update(ana["raw_extra"])
        except Exception:
            pass

        # -- 亚让 --
        try:
            asian_html = self._get(_ASIAN_URL.format(mid=mid))
            rows = self._parse_odds_table(asian_html, "asian")
            if rows:
                match["raw"]["asian_companies"] = rows
                ref = next((r for r in rows if r["company"] == "澳*"), rows[0])
                hc = parse_ah_number(ref["live_line"])
                ohc = parse_ah_number(ref["open_line"])
                if hc is not None:
                    match["asian"] = {
                        "handicap": hc,
                        "home_water": _num(ref["live_water1"]),
                        "opening_handicap": ohc,
                        "opening_home_water": _num(ref["open_water1"]),
                        "bookmaker": ref["company"],
                    }
            # 指数走势（亚指变动曲线）：opt-in，每家公司 2 次请求，批量慎用。
            if with_trends and rows:
                trends = {}
                for cid, cname in self.trend_company_ids(asian_html):
                    t = self.fetch_company_trend(mid, cid, "asian")
                    if t:
                        trends[cname] = t
                if trends:
                    match["raw"]["asian_movement"] = trends
                    match["raw"]["asian_movement_meta"] = {
                        "granularity": "key_change_points",
                        "n_companies": len(trends),
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    }
        except Exception:
            pass

        # -- 大小球 --
        try:
            ou_html = self._get(_OU_URL.format(mid=mid))
            rows = self._parse_odds_table(ou_html, "ou")
            if rows:
                match["raw"]["ou_companies"] = rows
                ref = next((r for r in rows if r["company"] == "澳*"), rows[0])
                line = parse_ou_number(ref["live_line"])
                if line is not None:
                    match["ou_line"] = line
                    match["raw"]["ou_bookmaker"] = ref["company"]
            # 指数走势（大小球变动曲线）：opt-in，同亚指。
            if with_trends and rows:
                trends = {}
                for cid, cname in self.trend_company_ids(ou_html):
                    t = self.fetch_company_trend(mid, cid, "ou")
                    if t:
                        trends[cname] = t
                if trends:
                    match["raw"]["ou_movement"] = trends
                    match["raw"]["ou_movement_meta"] = {
                        "granularity": "key_change_points",
                        "n_companies": len(trends),
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    }
        except Exception:
            pass

        # -- 角球（有则解析） --
        try:
            rows = self._parse_odds_table(self._get(_CORNER_URL.format(mid=mid)),
                                          "corner")
            if rows:
                match["raw"]["corner_companies"] = rows
        except Exception:
            pass

        match["snapshot_at"] = datetime.now(timezone.utc).isoformat()
        match["raw"]["collection_completed_at"] = match["snapshot_at"]
        return match

    def fetch_matches(self, with_trends: bool = False) -> list[dict]:
        matches = []
        for info in self._upcoming_ids():
            try:
                m = self._build_match(info, with_trends=with_trends)
            except Exception:
                continue
            if m:
                matches.append(m)
        return self.validate(matches)

    def get_totals(self, mid: str | int) -> dict | None:
        """单场大小球（总进球）盘口：各公司初盘/即时。

        必抓项（用户 2026-10-05 明确要求：大小球与欧赔/亚盘同级，
        daily_fetch 必须调用，不得以"缺失"代替抓取）。
        返回:
        {
          "mid": str, "n_companies": int,
          "ref": {"company","line","over_water","under_water",
                  "open_line","open_over_water","open_under_water"} | None,
          "companies": [{"company","open_line","open_over","open_under",
                         "live_line","live_over","live_under"}, ...],
          "captured_at": iso,
        }
        抓不到返回 None（调用方记 missing，不硬编）。
        参考公司优先取"澳*"（澳客），无则取第一家。
        """
        mid = str(mid)
        try:
            html = self._get(_OU_URL.format(mid=mid))
        except Exception:
            return None
        rows = self._parse_odds_table(html, "ou")
        if not rows:
            return None
        companies = [{
            "company": r["company"],
            "open_line": parse_ou_number(r["open_line"]),
            "open_over": _num(r["open_water1"]),
            "open_under": _num(r["open_water2"]),
            "live_line": parse_ou_number(r["live_line"]),
            "live_over": _num(r["live_water1"]),
            "live_under": _num(r["live_water2"]),
        } for r in rows]
        ref = next((r for r in rows if r["company"] == "澳*"), rows[0])
        return {
            "mid": mid,
            "n_companies": len(rows),
            "ref": {
                "company": ref["company"],
                "line": parse_ou_number(ref["live_line"]),
                "over_water": _num(ref["live_water1"]),
                "under_water": _num(ref["live_water2"]),
                "open_line": parse_ou_number(ref["open_line"]),
                "open_over_water": _num(ref["open_water1"]),
                "open_under_water": _num(ref["open_water2"]),
            },
            "companies": companies,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "source": "titan007",
        }
