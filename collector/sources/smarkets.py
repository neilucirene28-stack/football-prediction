"""
Smarkets Exchange API 接入 — 第二交易所赔率源
https://api.smarkets.com/v3/

完全免费，无需 key、无需注册。提供交易所订单簿（bids/offers + quantity），
可算买卖中点做独立市场信号。

注意：v3 REST 的 market 对象没有 volume/成交额字段——这是赔率源，
不是成交量源（成交量只有 Matchbook 有）。

覆盖: Smarkets 上有盘的所有足球赛事（含低级别联赛）
用途: 1X2 买卖中点（mid_1/mid_x/mid_2），与 Titan007/澳客市场交叉验证，
      drift 信号的第二交易所参照。

防泄漏：只取 start > 抓取时刻 且 state=upcoming 的未开赛场次。

实测要点（2026-10-05）:
- 事件列表: /v3/events/?type=football_match&state=upcoming&limit=20&sort=start_datetime,id
  （limit>30 会返回空；sort 取值受限，用 start_datetime,id；分页靠
  pagination.next_page 的 pagination_last_id/pagination_last_start_datetime）
- 比赛盘口: /v3/events/{id}/markets/ → market_type.name == "WINNER_3_WAY"
  即全场胜平负（slug "winner"）
- 合约: /v3/markets/{mid}/contracts/ → contract_type.name: HOME/DRAW/AWAY
- 订单簿: /v3/markets/{mid}/quotes/ → 按 contract_id 分组，每组合约有
  bids/offers: [{price, quantity}]；price 单位是万分比概率
  （如 9091 = 90.91%，对应十进制赔率 10000/9091 ≈ 1.10）
"""

import time
from datetime import datetime, timezone, timedelta

from _http import fetch_with_retry

BASE_URL = "https://api.smarkets.com/v3"
FOOTBALL_TOP_ID = "121005"
POLITE_DELAY = 0.5  # 每次请求后礼貌间隔（秒）


def _request(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    data = fetch_with_retry(url, timeout=25)
    time.sleep(POLITE_DELAY)
    return data


def _split_name(name):
    """'Home vs Away' -> (home, away)；解析失败返回 (None, None)"""
    if not name or " vs " not in name:
        return None, None
    parts = name.split(" vs ")
    if len(parts) != 2:
        return None, None
    return parts[0].strip(), parts[1].strip()


def _parse_dt(raw):
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def get_upcoming_events(asof=None, hours_ahead=72, max_events=80):
    """
    未开赛足球比赛事件（按开球时间升序）。
    返回: list of {event_id, home, away, start (datetime), source}
    """
    if asof is None:
        asof = datetime.now(timezone.utc)
    cutoff = asof + timedelta(hours=hours_ahead)
    out = []
    params = {
        "type": "football_match",
        "state": "upcoming",
        "limit": 20,
        "sort": "start_datetime,id",
    }
    seen = set()
    while len(out) < max_events:
        data = _request("/events/", params)
        events = data.get("events", []) or []
        if not events:
            break
        for e in events:
            eid = str(e.get("id", ""))
            if eid in seen:
                continue
            seen.add(eid)
            start = _parse_dt(e.get("start_datetime"))
            if start is None or start <= asof:
                continue
            if start > cutoff:
                # 按开球升序，越过窗口即可停
                return out
            home, away = _split_name(e.get("name", ""))
            if not home or not away:
                continue
            out.append({
                "event_id": eid,
                "home": home,
                "away": away,
                "start": start,
                "source": "smarkets",
            })
        # 分页
        if len(out) >= max_events:
            return out[:max_events]
        nxt = (data.get("pagination") or {}).get("next_page")
        if not nxt:
            break
        # next_page 形如 "?state=upcoming&type=football_match&...&pagination_last_id=.."
        params = {}
        for kv in nxt.lstrip("?").split("&"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k] = v
        if "limit" not in params:
            params["limit"] = 20
    return out


def _find_1x2_market(event_id):
    """找全场胜平负盘口（market_type.name == WINNER_3_WAY），返回 market_id 或 None"""
    data = _request(f"/events/{event_id}/markets/")
    for m in data.get("markets", []) or []:
        mt = (m.get("market_type") or {}).get("name", "")
        if mt == "WINNER_3_WAY" and m.get("state") == "open":
            return str(m.get("id"))
    return None


def _contracts_map(market_id):
    """contract_id -> outcome ('home'/'draw'/'away')"""
    data = _request(f"/markets/{market_id}/contracts/")
    mapping = {}
    for c in data.get("contracts", []) or []:
        ctype = (c.get("contract_type") or {}).get("name", "").upper()
        if ctype == "HOME":
            mapping[str(c.get("id"))] = "home"
        elif ctype == "DRAW":
            mapping[str(c.get("id"))] = "draw"
        elif ctype == "AWAY":
            mapping[str(c.get("id"))] = "away"
    return mapping


def _midpoint_decimal(bids, offers):
    """
    买卖中点 → 十进制赔率。price 为万分比概率（如 9091=90.91%）。
    无任一侧报价返回 None。
    """
    try:
        best_bid = max(float(b.get("price", 0)) for b in (bids or []))
        best_offer = min(float(o.get("price", 0)) for o in (offers or []) if float(o.get("price", 0)) > 0)
    except (ValueError, TypeError):
        return None, None
    if best_bid <= 0 or best_offer <= 0:
        return None, None
    mid = (best_bid + best_offer) / 2.0
    if mid <= 0:
        return None, None
    decimal = round(10000.0 / mid, 3)
    spread = round((best_offer - best_bid) / 10000.0, 4)  # 买卖价差（概率单位）
    return decimal, spread


def get_match_1x2(event_id):
    """
    某场比赛的 1X2 买卖中点赔率。
    返回: {market_id, mid_1, mid_x, mid_2, spread_1, spread_x, spread_2}
          任一 outcome 无报价则对应值为 None；找不到盘口返回 None。
    """
    mid = _find_1x2_market(event_id)
    if not mid:
        return None
    cmap = _contracts_map(mid)
    if not cmap:
        return None
    quotes = _request(f"/markets/{mid}/quotes/")
    if not isinstance(quotes, dict):
        return None
    res = {"market_id": mid, "mid_1": None, "mid_x": None, "mid_2": None,
           "spread_1": None, "spread_x": None, "spread_2": None}
    key_map = {"home": ("mid_1", "spread_1"), "draw": ("mid_x", "spread_x"),
               "away": ("mid_2", "spread_2")}
    for cid, outcome in cmap.items():
        q = quotes.get(cid) or {}
        dec, spr = _midpoint_decimal(q.get("bids"), q.get("offers"))
        mk, sk = key_map[outcome]
        res[mk], res[sk] = dec, spr
    return res


def get_upcoming_quotes(asof=None, hours_ahead=48, max_events=60):
    """
    未开赛场次 + 1X2 买卖中点（daily_fetch 用）。
    返回: list of {event_id, home, away, start, mid_1, mid_x, mid_2,
                  spread_1, spread_x, spread_2, market_id, source}
    无 1X2 盘口或无报价的场次自动跳过（记入 skipped 计数，调用方可打日志）。
    """
    out = []
    skipped = 0
    for e in get_upcoming_events(asof=asof, hours_ahead=hours_ahead,
                                 max_events=max_events):
        try:
            q = get_match_1x2(e["event_id"])
        except Exception:
            skipped += 1
            continue
        if not q or not q.get("mid_1"):
            skipped += 1
            continue
        row = dict(e)
        row.update(q)
        out.append(row)
    return out, skipped


if __name__ == "__main__":
    print("=== Smarkets 1X2 买卖中点测试 ===")
    rows, skipped = get_upcoming_quotes(hours_ahead=72, max_events=8)
    print(f"拿到报价: {len(rows)} 场，跳过: {skipped}")
    for r in rows:
        st = r["start"].strftime("%m-%d %H:%M")
        print(f"  {st} {r['home']} vs {r['away']}: "
              f"{r['mid_1']}/{r['mid_x']}/{r['mid_2']}")
