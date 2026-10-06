"""北单官方SP数据源 — 澳客即时SP(六玩法)+开奖 / 500彩票交叉验证

用户 2026-10-06 批准接入默认管线（原话"接"）。

澳客（主）:
- 在售即时SP: https://www.okooo.com/BJBet/BJBetMatchPoolOddsList.php?LotteryType={WDL,Score,HalfFull,TotalGoals,OverUnder,WL}
  静态 GB2312，无需登录。WDL 口径 69 行（=26102 期在售数）。
  - WDL: 胜平负SP；对阵列括号内为官方让球数（如"韩国 (-1) VS 乌兹别克斯坦"，
    无括号=该场未开让球胜平负）
  - Score: 比分SP（每场占3行：胜区10项/平区5项/负区10项）
  - HalfFull: 半全场SP（澳客只列8项，无"负负"）
  - TotalGoals: 总进球SP（0/1/2/3/4/5/6/7+）
  - OverUnder: 上下单双SP（上单/上双/下单/下双）
  - WL: 澳客自有让球胜负盘（0.5盘口，仅胜/负两项），与官方北单让球胜平负
    （整数让球、让胜/让平/让负三项）不是同一玩法，仅作额外市场信号
- 开奖: https://www.okooo.com/danchang/kaijiang/?LotteryNo={期号}
  六玩法赛果+SP一次齐（半场比分/全场比分/让球数/让球赛果与SP/比分与SP/
  总进球与SP/半全场与SP/上下单双与SP），回测标签黄金来源。
  让球赛果编码 3=让胜/1=让平/0=让负；半全场如"1-3"=平胜（3=胜/1=平/0=负）。

500彩票（备）: https://trade.500.com/bjdc/project_fq_{bf,bq,ds,jq}.php
  GBK 静态；各玩法 SP 多数未开售显示(--)，仅表头行含胜平负SP可作交叉验证。

抓取纪律: GB2312/GBK 解码、带 Referer、页间间隔≥2s（澳客 WAF 快抓会 405，
405 自动重试）。
"""

import re
import time
from datetime import datetime

from _http import fetch_with_retry, HTTPError

OKOOO_BASE = "https://www.okooo.com"
W500_BASE = "https://trade.500.com/bjdc"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Referer": "https://www.okooo.com/"}
W500_HEADERS = {"User-Agent": UA, "Referer": "https://trade.500.com/bjdc/"}
POLITE_DELAY = 2  # 页间间隔（澳客 WAF 敏感；单页内不 sleep）

PLAY_TYPES = ("WDL", "Score", "HalfFull", "TotalGoals", "OverUnder", "WL")
W500_PAGES = ("project_fq_bf", "project_fq_bq", "project_fq_ds",
              "project_fq_jq")


def _get_html(url, headers, encoding, retries=3):
    """带 405 重试的抓取。"""
    last = None
    for _ in range(retries):
        time.sleep(POLITE_DELAY)
        try:
            raw = fetch_with_retry(url, headers=headers, timeout=30,
                                   parse_json=False)
            return raw.decode(encoding, "ignore")
        except HTTPError as e:
            last = e
            if getattr(e, "code", None) != 405:
                raise
            time.sleep(8)
    raise last


def _clean(s):
    return re.sub(r"<[^>]+>", "", s).strip()


def _strip_comments(html):
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def parse_matchup(s):
    """"韩国 (-1) VS 乌兹别克斯坦" -> (home, away, handicap)。
    无括号时 handicap 为 None。"""
    s = re.sub(r"\s+", " ", s).strip()
    m = re.match(r"^(.*?)(?:\s*\(([^)]*)\))?\s+VS\s+(.*)$", s, re.I)
    if not m:
        return s, "", None
    home, paren, away = m.group(1).strip(), m.group(2), m.group(3).strip()
    handicap = None
    if paren:
        hm = re.search(r"[+-]?\d+(?:\.\d+)?", paren)
        if hm:
            handicap = float(hm.group(0))
    return home, away, handicap


def _kickoff(s, today=None):
    """"10-06 19:00" -> "2026-10-06 19:00"。跨年兜底：1月且当前12月则年份+1。"""
    s = s.strip()
    m = re.match(r"(\d{2})-(\d{2})\s+(\d{2}:\d{2})", s)
    if not m:
        return s
    today = today or datetime.now()
    year = today.year
    if m.group(1) == "01" and today.month == 12:
        year += 1
    return f"{year}-{m.group(1)}-{m.group(2)} {m.group(3)}"


def _table_rows(html):
    m = re.search(r'<table id="TableBorder".*?</table>', html, re.S)
    if not m:
        return []
    return re.findall(r"<tr.*?</tr>", m.group(0), re.S)


def _row_cells(row):
    return [_clean(c).replace("\r", " ").replace("\n", " ")
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]


def _num(s):
    """SP 文本 -> float；空/"--" -> None。"""
    s = (s or "").strip()
    if not s or s == "--":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_score_rows(rows):
    """比分页专用：3 个 label 区行（胜/平/负）+ 每场 3 个值行。"""
    win_labels, draw_labels, lose_labels = [], [], []
    out, cur, zone = [], None, None
    for r in rows:
        cells = _row_cells(r)
        if not cells:
            continue
        head = cells[0]
        if head.startswith("\u5e8f\u53f7") and len(cells) > 7:
            win_labels = [c for c in cells[6:] if c]
            zone = "want_draw_labels"
            continue
        if zone == "want_draw_labels" and head == "\u5e73\u5176\u4ed6":
            draw_labels = [c for c in cells if c]
            zone = "want_lose_labels"
            continue
        if zone == "want_lose_labels" and head == "\u8d1f\u5176\u4ed6":
            lose_labels = [c for c in cells if c]
            zone = None
            continue
        if (re.fullmatch(r"\d+", head or "") and len(cells) > 6
                and " VS " in (cells[2] if len(cells) > 2 else "")):
            home, away, handicap = parse_matchup(cells[2])
            cur = {
                "seq": head, "league": cells[1],
                "kickoff": _kickoff(cells[3]), "home": home,
                "away": away, "handicap": handicap,
                "play": "Score", "sp": {},
            }
            for lab, v in zip(win_labels, cells[6:]):
                cur["sp"][lab] = _num(v)
            out.append(cur)
            zone = "want_draw"
        elif cur is not None and zone == "want_draw":
            vals = [c for c in cells if c]
            for lab, v in zip(draw_labels, vals):
                cur["sp"][lab] = _num(v)
            zone = "want_lose"
        elif cur is not None and zone == "want_lose":
            vals = [c for c in cells if c]
            for lab, v in zip(lose_labels, vals):
                cur["sp"][lab] = _num(v)
            zone = None
    return out


def get_sp_page(lottery_type="WDL", html=None):
    """单玩法即时SP页解析。返回 [{seq, league, kickoff, home, away,
    handicap, play, sp}]，sp 为 {label: value}。"""
    if html is None:
        url = (f"{OKOOO_BASE}/BJBet/BJBetMatchPoolOddsList.php"
               f"?LotteryType={lottery_type}")
        html = _get_html(url, HEADERS, "gb2312")
    rows = _table_rows(html)
    if lottery_type == "Score":
        return _parse_score_rows(rows)
    out, labels = [], []
    for r in rows:
        cells = _row_cells(r)
        if not cells:
            continue
        if cells[0].startswith("\u5e8f\u53f7") and len(cells) > 7:
            labels = [c for c in cells[6:] if c]
            continue
        if cells[0].startswith("\u5e8f\u53f7"):
            continue
        if (re.fullmatch(r"\d+", cells[0] or "") and len(cells) > 6
                and " VS " in (cells[2] if len(cells) > 2 else "")):
            home, away, handicap = parse_matchup(cells[2])
            sp = {}
            for lab, v in zip(labels, cells[6:]):
                sp[lab] = _num(v)
            out.append({
                "seq": cells[0], "league": cells[1],
                "kickoff": _kickoff(cells[3]), "home": home,
                "away": away, "handicap": handicap,
                "play": lottery_type, "sp": sp,
            })
    return out


def get_all_sp(html_pages=None):
    """六玩法即时SP一次抓齐，按 (home, away) 合并。

    返回 { (home, away): {seq, league, kickoff, handicap,
            sp_wdl, sp_score, sp_half_full, sp_total_goals, sp_over_under,
            wl_handicap, sp_wl} }。"""
    html_pages = html_pages or {}
    merged = {}
    for pt in PLAY_TYPES:
        try:
            rows = get_sp_page(pt, html=html_pages.get(pt))
        except Exception:
            continue
        key = {
            "WDL": "sp_wdl", "Score": "sp_score",
            "HalfFull": "sp_half_full", "TotalGoals": "sp_total_goals",
            "OverUnder": "sp_over_under", "WL": "sp_wl",
        }[pt]
        for r in rows:
            k = (r["home"], r["away"])
            m = merged.setdefault(k, {
                "seq": r["seq"], "league": r["league"],
                "kickoff": r["kickoff"], "home": r["home"],
                "away": r["away"], "handicap": r["handicap"],
            })
            if pt == "WL":
                m["wl_handicap"] = r["handicap"]
            m[key] = r["sp"]
    return merged


_RQ_MAP = {"3": "让胜", "1": "让平", "0": "让负"}
_HF_MAP = {"3": "胜", "1": "平", "0": "负"}


def _decode_half_full(s):
    m = re.match(r"([301])-([301])", (s or "").strip())
    if not m:
        return s
    return _HF_MAP[m.group(1)] + _HF_MAP[m.group(2)]


def get_kaijiang(lottery_no, html=None):
    """开奖页解析：六玩法赛果+SP。返回 [{seq, league, kickoff, home, away,
    half_score, full_score, handicap, rq_result, rq_sp, score_result,
    score_sp, goals, goals_sp, half_full, half_full_sp, ou, ou_sp}]。"""
    if html is None:
        url = f"{OKOOO_BASE}/danchang/kaijiang/?LotteryNo={lottery_no}"
        html = _get_html(url, HEADERS, "gb2312")
    html = _strip_comments(html)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)
    out = []
    for r in rows:
        cells = [_clean(c) for c in
                 re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if len(cells) < 18 or not re.fullmatch(r"\d+", cells[0] or ""):
            continue
        try:
            handicap = float(cells[7]) if cells[7] else None
        except ValueError:
            handicap = None
        out.append({
            "seq": cells[0], "league": cells[1],
            "kickoff": _kickoff(cells[2]), "home": cells[3],
            "away": cells[4], "half_score": cells[5],
            "full_score": cells[6], "handicap": handicap,
            "rq_result": _RQ_MAP.get(cells[8], cells[8]),
            "rq_sp": _num(cells[9]),
            "score_result": cells[10], "score_sp": _num(cells[11]),
            "goals": int(cells[12]) if cells[12].isdigit() else None,
            "goals_sp": _num(cells[13]),
            "half_full": _decode_half_full(cells[14]),
            "half_full_sp": _num(cells[15]),
            "ou": cells[16], "ou_sp": _num(cells[17]),
        })
    return out


def get_500_headers(html=None, pages=None):
    """500彩票表头行交叉验证：每页表头含胜平负SP。
    返回 [{seq, league, kickoff, home, away, handicap, sp_w, sp_d, sp_l}]。"""
    if html is None:
        out = []
        for p in (pages or W500_PAGES):
            url = f"{W500_BASE}/{p}.php"
            try:
                h = _get_html(url, W500_HEADERS, "gbk")
            except Exception:
                continue
            out.extend(parse_500_headers(h))
        # 按 (home, away) 去重
        seen, dedup = set(), []
        for r in out:
            k = (r["home"], r["away"])
            if k not in seen:
                seen.add(k)
                dedup.append(r)
        return dedup
    return parse_500_headers(html)


def parse_500_headers(html):
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        txt = re.sub(r"<[^>]+>", " ", r)
        txt = re.sub(r"\s+", " ", txt).strip()
        # "18 友谊赛 18:50 [世23] 韩国 -1 乌兹别克斯坦 [世58] 1.64 3.82 4.71 ..."
        m = re.match(
            r"^(\d+)\s+(\S+)\s+(\d{2}:\d{2})\s+\[([^\]]*)\]\s+(.+?)\s+"
            r"([+-]?\d+(?:\.\d+)?)\s+(.+?)\s+\[([^\]]*)\]\s+"
            r"(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)", txt)
        if not m:
            continue
        out.append({
            "seq": m.group(1), "league": m.group(2),
            "kickoff": m.group(3), "home_rank": m.group(4),
            "home": m.group(5).strip(), "handicap": float(m.group(6)),
            "away": m.group(7).strip(), "away_rank": m.group(8),
            "sp_w": float(m.group(9)), "sp_d": float(m.group(10)),
            "sp_l": float(m.group(11)),
        })
    return out
