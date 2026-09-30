"""小店火真实采集器：Playwright 驱动真实页面，读取渲染后的 DOM。

为什么不用 HTTP 直调：站内 JDD 接口请求参数加密、响应解密
（2026-09-29 浏览器实测：裸调 https://apic-sport-new.jdddata.com/...
返回失败），逆向 crypto-js 极易随改版失效。DOM 是官方渲染结果，
字段稳定可验证。

只读公开数据；不登录、不点解锁/订阅/购买；请求节流。

环境变量：
  XDH_ENTRY_URL   店铺入口（默认公开进店链接，进店码 5186）
  XDH_ENTRY_CODE  进店码（默认 5186）
  XDH_MAX_MATCHES 单次最多抓详情的场次（默认 15）
  XDH_DAYS_AHEAD  只抓未来 N 天内（默认 7）
  XDH_HEADLESS    设为 0 则有头模式（默认 1）
"""
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .base import Source

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 中文盘口 → 数字（主队视角；“受让”类为负）
HANDICAP_MAP = {
    "平手": 0.0, "平/半": 0.25, "半球": 0.5, "半/一": 0.75,
    "一球": 1.0, "一/球半": 1.25, "球半": 1.5, "球半/两球": 1.75,
    "两球": 2.0, "两/两球半": 2.25, "两球半": 2.5,
    "受平/半": -0.25, "受半球": -0.5, "受半/一": -0.75,
    "受一球": -1.0, "受一/球半": -1.25, "受球半": -1.5,
}


def _parse_handicap(text: str):
    text = (text or "").strip()
    if text in HANDICAP_MAP:
        return HANDICAP_MAP[text]
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _num(text: str):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def _tab_tokens(page) -> list[str]:
    """当前 tab 的 body 文本 → 扁平 token 列表。

    网站用 div 渲染、无 <table>，单元格之间以空行分隔，
    故按行切分去空即得 token 流。
    """
    try:
        body = page.locator("body").inner_text(timeout=8000)
    except Exception:  # noqa: BLE001
        return []
    return [t.strip() for t in body.splitlines() if t.strip()]


_DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{2}$")
_SCORE_RE = re.compile(r"^(\d{1,2})-(\d{1,2})$")


def _rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in re.split(r"\t+", line.strip()) if c.strip()]
        if cells:
            rows.append(cells)
    return rows


def _base_name(name: str) -> str:
    """队名归一：列表页"朝鲜女" vs 详情页"朝鲜女足" → "朝鲜"。"""
    return re.sub(r"(女足|男足|女队|男队|女子|男子|[男女])$", "", name.strip())


class XiaoDianHuoSource(Source):
    name = "xiaodianhuo"

    def __init__(self, entry_url: str = "", headless: bool | None = None):
        self.entry_url = (entry_url or os.environ.get("XDH_ENTRY_URL")
                          or "https://prd.91honghuo.com/c/1Mk4R187DG_f")
        self.entry_code = os.environ.get("XDH_ENTRY_CODE", "5186")
        self.max_matches = int(os.environ.get("XDH_MAX_MATCHES", "15"))
        self.days_ahead = int(os.environ.get("XDH_DAYS_AHEAD", "7"))
        self.headless = (os.environ.get("XDH_HEADLESS", "1") != "0"
                         if headless is None else headless)
        self._station = {"station_user_id": "4361946",
                         "station_uuid": "435cfd3ao8hqmt1708801999"}

    # ---------- 列表 ----------
    def _enter_shop(self, page) -> None:
        page.goto(self.entry_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        # 进店码输入框（若有）
        try:
            box = page.locator("input").first
            if box.is_visible(timeout=4000):
                box.fill(self.entry_code)
                page.wait_for_timeout(800)
                for sel in ["button:has-text('确定')", "button:has-text('进入')",
                            "button"]:
                    try:
                        page.locator(sel).first.click(timeout=3000)
                        break
                    except Exception:  # noqa: BLE001
                        continue
                page.wait_for_timeout(3000)
        except Exception:  # noqa: BLE001
            pass
        # 记住站点身份参数
        m = re.search(r"station_user_id=(\d+)", page.url)
        if m:
            self._station["station_user_id"] = m.group(1)
        m = re.search(r"station_uuid=([a-zA-Z0-9]+)", page.url)
        if m:
            self._station["station_uuid"] = m.group(1)

    def _get_match_list(self, page) -> list[dict]:
        """解析比赛卡片：联赛/开赛时间/主客队/官方赔率。

        卡片不是 <a> 包裹（浏览器验证 2026-09-29），点击靠 JS 跳转；
        卡片内无日期，日期取自页面头"2026-09-29 星期二"。
        返回的每项带 _card_idx，供 fetch_matches 点击进入详情。
        """
        self._enter_shop(page)
        page.wait_for_timeout(4000)
        body = page.locator("body").inner_text(timeout=8000)
        date_m = re.search(r"(20\d{2}-\d{2}-\d{2})", body)
        page_date = (date_m.group(1) if date_m
                     else datetime.now(TZ).strftime("%Y-%m-%d"))
        texts = []
        try:
            items = page.get_by_text(re.compile(r"\d+条情报")).all()
        except Exception:  # noqa: BLE001
            items = []
        for it in items:
            try:
                text = it.evaluate(
                    "el => { let n = el; while (n && n.parentElement && "
                    "(n.innerText || '').trim().length < 40) "
                    "n = n.parentElement; return (n.innerText || ''); }")
            except Exception:  # noqa: BLE001
                continue
            if text and "vs" in text.lower():
                texts.append(text)
        # 去重保序
        seen, uniq = set(), []
        for t in texts:
            key = t[:80]
            if key not in seen:
                seen.add(key)
                uniq.append(t)
        out = []
        for i, t in enumerate(uniq):
            m = self._parse_card(t, page_date)
            if m:
                m["_card_idx"] = i
                out.append(m)
        return out

    def _parse_card(self, card_text: str, page_date: str) -> dict | None:
        lines = [ln.strip() for ln in card_text.splitlines() if ln.strip()]
        text = "\n".join(lines)
        time_m = re.search(r"(\d{1,2}:\d{2})", text)
        if not time_m:
            return None
        # 队名：①单行 "A vs B"；②多行布局（A / vs / B 各占一行）
        home = away = ""
        vs_pat = (r"([\u4e00-\u9fa5A-Za-z0-9. '\-]{2,14})\s*(?:vs|VS|ＶＳ|对阵)\s*"
                  r"([\u4e00-\u9fa5A-Za-z0-9. '\-]{2,14})")
        for ln in lines:
            m = re.search(vs_pat, ln)
            if m:
                home, away = m.group(1).strip(), m.group(2).strip()
                break

        def _team_token(ln: str) -> str:
            m = re.search(r"[\u4e00-\u9fa5A-Za-z0-9.']{2,14}", ln)
            return m.group(0).strip() if m else ""

        if not home:
            for i, ln in enumerate(lines):
                if ln.strip().lower() in ("vs", "对阵"):
                    up = next((_team_token(l) for l in reversed(lines[:i])
                               if _team_token(l)), "")
                    dn = next((_team_token(l) for l in lines[i + 1:]
                               if _team_token(l)), "")
                    home, away = up, dn
                    break
        if not home:
            names = re.findall(r"[\u4e00-\u9fa5]{2,8}", text)
            league_ln = next((ln for ln in lines if re.search(
                r"杯|联赛|[超甲乙丙]级?|女足|男足", ln)), "")
            cands = [n for n in names
                     if n not in league_ln and "情报" not in n
                     and not n.isdigit()]
            if len(cands) >= 2:
                home, away = cands[0], cands[1]
        if not home or not away or home == away:
            return None
        kickoff = f"{page_date} {time_m.group(1)}:00"
        iso = _beijing_to_iso(kickoff)
        if not iso:
            return None
        league = next((ln for ln in lines if re.search(
            r"杯|联赛|[超甲乙丙]级?|女足|男足", ln)
            and home not in ln and away not in ln), "")
        out = {
            "external_id": "",  # 点击进详情后按真实 matchid 回填
            "competition": league[:12],
            "home_team": home, "away_team": away,
            "kickoff_at": iso, "status": "scheduled",
        }
        for ln in lines:
            nums = re.findall(r"\d+\.\d{2}", ln)
            if len(nums) == 3 and home not in ln:
                out["odds"] = {"home": float(nums[0]), "draw": float(nums[1]),
                               "away": float(nums[2])}
                break
        return out

    # ---------- 详情 ----------
    def _click_tab(self, page, name: str) -> bool:
        try:
            page.get_by_text(name, exact=True).first.click(timeout=5000)
            page.wait_for_timeout(3500)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _parse_history_tab(self, page, home: str, away: str) -> dict:
        """战绩：近10场 + 交锋 → home_recent/away_recent/h2h。

        div token 流；section 顺序固定为 主队近 → 客队近 → 交锋。
        注意交锋区内有一条"主队近10场…胜…平…负"摘要行，
        section 已是 h2h 时不再回切。行以日期 token 为锚点：
        赛事|日期|t1|排名|比分|t2|排名|赛果。
        """
        toks = _tab_tokens(page)
        section = None
        recs: list[dict] = []
        for i, t in enumerate(toks):
            if t == "交锋":
                section = "h2h"
                continue
            if section != "h2h":
                if "主队近" in t and section is None:
                    section = "home"
                    continue
                if "客队近" in t and section == "home":
                    section = "away"
                    continue
            if _DATE_RE.match(t) and i + 4 < len(toks):
                t1, sc, t2 = toks[i + 1], toks[i + 3], toks[i + 4]
                m_sc = _SCORE_RE.match(sc or "")
                if not m_sc or _DATE_RE.match(sc):
                    continue
                comp = toks[i - 1] if i > 0 else ""
                recs.append({"section": section, "comp": comp, "date": t,
                             "t1": t1, "s1": int(m_sc.group(1)),
                             "t2": t2, "s2": int(m_sc.group(2))})

        detail: dict = {}
        home_recent, away_recent, h2h = [], [], []
        for r in recs:
            t1b = _base_name(r["t1"])
            t2b = _base_name(r["t2"])
            if r["section"] == "h2h":
                mine = (t1b == home or home in t1b)
                gf, ga = (r["s1"], r["s2"]) if mine else (r["s2"], r["s1"])
                h2h.append({"gf": gf, "ga": ga})
                continue
            team = None
            if t1b == home or home in t1b or t2b == home or home in t2b:
                team = "home"
            elif t1b == away or away in t1b or t2b == away or away in t2b:
                team = "away"
            if team is None:
                continue
            base = home if team == "home" else away
            mine_home = (t1b == base or base in t1b)
            gf, ga = (r["s1"], r["s2"]) if mine_home else (r["s2"], r["s1"])
            rec = {"gf": gf, "ga": ga, "venue": "H" if mine_home else "A",
                   "date": r["date"], "comp": r["comp"]}
            (home_recent if team == "home" else away_recent).append(rec)
        if home_recent:
            detail["home_recent"] = home_recent[:10]
        if away_recent:
            detail["away_recent"] = away_recent[:10]
        if h2h:
            detail["h2h"] = h2h[:10]
        return detail

    def _grab_odds_row(self, toks: list[str], label: str) -> dict | None:
        """典型指数行：label 后连续 6 个数字 = 初胜/平/负 + 即胜/平/负。"""
        for i, t in enumerate(toks):
            if t != label:
                continue
            cells = toks[i + 1:i + 13]
            if not cells or _num(cells[0]) is None:
                continue
            nums: list[float] = []
            for x in cells:
                if x == ">":
                    break
                n = _num(x)
                if n is None:
                    break
                nums.append(n)
            if len(nums) >= 6:
                o, l = nums[:3], nums[3:6]
                return {"opening": {"home": o[0], "draw": o[1], "away": o[2]},
                        "live": {"home": l[0], "draw": l[1], "away": l[2]}}
        return None

    def _parse_europe_tab(self, page) -> dict:
        """欧指：典型指数表 → 百家平均初盘/即时（无则竞彩官方）。"""
        toks = _tab_tokens(page)
        detail: dict = {}
        for label in ("百家平均", "竞彩官方"):
            row = self._grab_odds_row(toks, label)
            if row:
                detail["odds"] = row["live"]
                detail["opening_odds"] = row["opening"]
                detail["odds_source"] = label
                if label == "百家平均":
                    detail["odds_avg"] = row
                break
        return detail

    _ASIAN_SOURCES = ("Bet365", "澳彩", "皇冠", "平博", "伟德",
                      "易胜博", "明升")

    def _grab_handicap_row(self, toks: list[str],
                           label: str) -> dict | None:
        """典型亚盘行：初水 初盘 初客水 即时水 即时盘 即时客水。"""
        for i, t in enumerate(toks):
            if t != label:
                continue
            cells = toks[i + 1:i + 9]
            if not cells or _num(cells[0]) is None:
                continue
            row = []
            for x in cells:
                if x == ">":
                    break
                row.append(x)
            if len(row) < 6:
                continue
            h_open = _parse_handicap(row[1])
            h_live = _parse_handicap(row[4])
            w_open, w_live = _num(row[0]), _num(row[3])
            if None in (h_open, h_live, w_open, w_live):
                continue
            # 引擎口径：负数=主让
            return {"source": label, "handicap_text": row[4],
                    "opening_handicap": -h_open, "handicap": -h_live,
                    "opening_home_water": w_open, "home_water": w_live}
        return None

    def _grab_ou_row(self, toks: list[str], label: str) -> dict | None:
        """典型大小球行：大水 盘口 小水 大水 盘口 小水。"""
        for i, t in enumerate(toks):
            if t != label:
                continue
            cells = toks[i + 1:i + 9]
            if not cells or _num(cells[0]) is None:
                continue
            row = []
            for x in cells:
                if x == ">":
                    break
                row.append(x)
            if len(row) < 6:
                continue
            line_open, line = _num(row[1]), _num(row[4])
            if line is None:
                continue
            return {"source": label, "line": line,
                    "opening_line": (line_open if line_open is not None
                                     else line),
                    "over_water": _num(row[3]), "under_water": _num(row[5]),
                    "opening_over_water": _num(row[0]),
                    "opening_under_water": _num(row[2])}
        return None

    def _parse_asia_tab(self, page) -> dict:
        """亚指：典型亚盘 + 大小球子 tab（情报/推荐为会员区，不碰）。"""
        detail: dict = {}
        toks = _tab_tokens(page)
        for label in self._ASIAN_SOURCES:
            row = self._grab_handicap_row(toks, label)
            if row:
                detail["asian"] = row
                break
        # 大小球子 tab
        try:
            page.get_by_text("大小球", exact=True).first.click(timeout=5000)
            page.wait_for_timeout(2500)
            toks2 = _tab_tokens(page)
            for label in self._ASIAN_SOURCES:
                row = self._grab_ou_row(toks2, label)
                if row:
                    detail["over_under"] = row
                    detail["ou_line"] = row["line"]
                    break
        except Exception:  # noqa: BLE001
            pass
        return detail

    def _parse_lineup_tab(self, page) -> dict:
        """阵容 → 伤停子 tab 无结构化伤停数据（仅密报广告），返回空。

        缺失如实标缺失，不编造。
        """
        return {}

    def _header_teams(self, toks: list[str]) -> tuple[str | None, str | None]:
        """详情页标题区取规范队名：`{队名} | €xxx万 ...` 出现两次。

        卡片与详情页的队名写法可能不同（如 枥木城 vs 枥木大平），
        战绩匹配必须用详情页内的名字，否则主队近况会漏抓。
        """
        teams: list[str] = []
        for i, t in enumerate(toks[:40]):
            if "€" in t and "万" in t and i > 0 and toks[i - 1] not in teams:
                teams.append(toks[i - 1])
            if len(teams) == 2:
                break
        if len(teams) == 2:
            return teams[0], teams[1]
        return None, None

    def _parse_detail(self, page, base: dict) -> dict:
        """调用时已在详情页（点击卡片跳转而来）；切 tab 逐个解析。"""
        page.wait_for_timeout(4000)
        h_detail, a_detail = self._header_teams(_tab_tokens(page))
        home = _base_name(h_detail) if h_detail else _base_name(base["home_team"])
        away = _base_name(a_detail) if a_detail else _base_name(base["away_team"])
        detail: dict = {}
        if h_detail:
            detail["home_team_detail"] = h_detail
            detail["away_team_detail"] = a_detail
        if self._click_tab(page, "战绩"):
            detail.update(self._parse_history_tab(page, home, away))
        if self._click_tab(page, "欧指"):
            detail.update(self._parse_europe_tab(page))
        if self._click_tab(page, "亚指"):
            detail.update(self._parse_asia_tab(page))
        if self._click_tab(page, "阵容"):
            detail.update(self._parse_lineup_tab(page))
        return detail

    def _open_detail(self, page, card_idx: int) -> str | None:
        """点击第 card_idx 张卡片，等待跳转，返回真实 matchid。"""
        try:
            loc = page.get_by_text(re.compile(r"\d+条情报")).nth(card_idx)
            loc.click(timeout=8000)
            page.wait_for_url(re.compile(r"matchid=\d+"), timeout=20000)
            m = re.search(r"matchid=(\d+)", page.url)
            return m.group(1) if m else None
        except Exception:  # noqa: BLE001
            return None

    # ---------- 主流程 ----------
    def fetch_matches(self) -> list[dict]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # noqa: F841
            raise RuntimeError(
                "需要 playwright：pip install playwright && "
                "python -m playwright install chromium") from e
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=self.days_ahead)
        matches: list[dict] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless,
                                         args=["--no-sandbox"])
            page = browser.new_page(user_agent=UA,
                                    viewport={"width": 1366, "height": 900})
            try:
                cards = self._get_match_list(page)
                for base in cards:
                    ko = datetime.fromisoformat(
                        base["kickoff_at"].replace("Z", "+00:00"))
                    if not (now < ko <= horizon):
                        continue
                    try:
                        mid = self._open_detail(page, base.pop("_card_idx"))
                        if not mid:
                            continue
                        base["external_id"] = f"xdh-{mid}"
                        detail = self._parse_detail(page, base)
                        page.go_back()
                        page.wait_for_timeout(2500)
                    except Exception as ex:  # noqa: BLE001
                        detail = {"_detail_error": str(ex)[:200]}
                        try:
                            page.go_back()
                            page.wait_for_timeout(2500)
                        except Exception:  # noqa: BLE001
                            pass
                    base.update({k: v for k, v in detail.items()
                                 if v is not None})
                    base["snapshot_at"] = datetime.now(timezone.utc).isoformat()
                    matches.append(base)
                    if len(matches) >= self.max_matches:
                        break
                    time.sleep(1.0)
            finally:
                browser.close()
        return self.validate(matches)


def _beijing_to_iso(s: str) -> str | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=TZ).isoformat()
        except ValueError:
            continue
    return None
