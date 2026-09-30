#!/usr/bin/env python3
"""Polymarket 只读适配（资金流向 MVP · 阶段 0）。

只用公开、免费、免登录的只读端点：
  gamma-api.polymarket.com/public-search  事件搜索
  gamma-api.polymarket.com/events         事件详情（含 markets/volume）
  gamma-api.polymarket.com/markets        市场详情（含 liquidity）

输出该场赛前的价格/成交量/流动性快照。搜不到、深度不够、
队名对不上 → {"status": "missing", ...}，绝不回填、绝不估算。

注意：volume 是市场累计成交额（USDC），无法回溯"开球前"那一刻的
快照 —— 因此只用于实时/准实时场景；历史回测中一律视为缺失，
降级用 Titan007 公司数量做流动性代理（见 engine/market_flow）。
"""
import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_STOP = {"fc", "cf", "sc", "ac", "afc", "united", "city", "club", "de", "la",
        "the", "real"}


def _get(path: str, params: dict | None = None, timeout: int = 20) -> object:
    url = GAMMA + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def normalize_team(name: str) -> str:
    t = (name or "").lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    toks = [w for w in t.split() if w not in _STOP and len(w) > 1]
    return " ".join(toks)


def _name_hit(title: str, norm: str) -> bool:
    if not norm:
        return False
    tl = title.lower()
    return all(tok in tl for tok in norm.split())


def search_events(query: str, limit: int = 10) -> list:
    try:
        d = _get("/public-search", {"q": query})
    except Exception:
        return []
    return d.get("events", [])[:limit]


def get_event(slug: str) -> dict | None:
    try:
        evts = _get("/events", {"slug": slug})
    except Exception:
        return None
    return evts[0] if evts else None


def _event_start(e: dict):
    for k in ("startTime", "startDate"):
        v = e.get(k)
        if v:
            try:
                dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def find_match_event(home: str, away: str, limit: int = 10,
                     asof: datetime | None = None) -> dict | None:
    """按两队名找最可能的赛事事件。

    asof: 目标时间（默认现在）；优先选最接近 asof 的未来场，
    没有未来场才选最近的过去场。队名对不上返回 None。
    """
    nh, na = normalize_team(home), normalize_team(away)
    target = asof or datetime.now(timezone.utc)
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    cands = []
    for q in (f"{home} {away}", f"{away} {home}"):
        for e in search_events(q, limit):
            title = e.get("title", "")
            score = (2 if _name_hit(title, nh) else 0) + \
                    (2 if _name_hit(title, na) else 0)
            if score < 3:  # 至少一队全中、另一队部分中
                continue
            start = _event_start(e)
            if start is None:
                gap = 9e9
            else:
                delta = (start - target).total_seconds()
                # 未来场优先：过去场加一个大罚项
                gap = abs(delta) + (4e9 if delta < 0 else 0)
            cands.append((score, gap, e))
        if cands:
            break
    if not cands:
        return None
    cands.sort(key=lambda x: (-x[0], x[1]))
    return cands[0][2]


def _price_of(mkt: dict):
    for key in ("lastTradePrice",):
        try:
            p = float(mkt.get(key))
            if 0 < p < 1:
                return p
        except (TypeError, ValueError):
            pass
    try:
        ops = json.loads(mkt.get("outcomePrices") or "[]")
        p = float(ops[0])
        if 0 < p < 1:
            return p
    except (TypeError, ValueError, IndexError):
        pass
    return None


def _volume_of(mkt: dict):
    try:
        v = float(mkt.get("volume") or 0)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def extract_flow(event: dict, home: str = "", away: str = "") -> dict:
    """从事件提取 1x2 式价格/成交量。拿不到 → missing。"""
    markets = event.get("markets") or []
    if not markets:
        return {"status": "missing", "reason": "事件无市场列表"}
    nh, na = normalize_team(home), normalize_team(away)
    # 情形 A：三个独立 Yes/No 市场（主胜/平局/客胜各一个）
    buckets = {"home": None, "draw": None, "away": None}
    for m in markets:
        q = (m.get("question") or "").lower()
        price, vol = _price_of(m), _volume_of(m)
        if price is None:
            continue
        key = None
        if "draw" in q or "tie" in q:
            key = "draw"
        elif "win" in q:
            if nh and _name_hit(q, nh):
                key = "home"
            elif na and _name_hit(q, na):
                key = "away"
        if key and buckets[key] is None:
            buckets[key] = {"price": price, "volume": vol,
                            "liquidity": m.get("liquidity"),
                            "question": m.get("question")}
    # 情形 B：用 outcomes 猜（单市场多结果）
    if not any(buckets.values()):
        for m in markets:
            try:
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = json.loads(m.get("outcomePrices") or "[]")
            except (TypeError, ValueError):
                continue
            if len(outcomes) == 3 and len(prices) == 3:
                try:
                    ps = [float(p) for p in prices]
                except (TypeError, ValueError):
                    continue
                if all(0 < p < 1 for p in ps):
                    vol = _volume_of(m)
                    return {"status": "ok",
                            "prices": {"home": ps[0], "draw": ps[1],
                                       "away": ps[2]},
                            "volume_usdc": vol,
                            "liquidity_usdc": m.get("liquidity"),
                            "n_markets": 1,
                            "event_slug": event.get("slug"),
                            "captured_at": datetime.now(timezone.utc)
                                           .isoformat()}
    filled = {k: v for k, v in buckets.items() if v}
    if len(filled) < 2:
        return {"status": "missing",
                "reason": f"可识别的 1x2 市场不足（{len(filled)}/3）"}
    vols = [v["volume"] for v in filled.values() if v["volume"]]
    return {"status": "ok" if len(filled) == 3 else "partial",
            "prices": {k: v["price"] for k, v in filled.items()},
            "volume_usdc": round(sum(vols), 2) if vols else None,
            "n_markets": len(filled),
            "event_slug": event.get("slug"),
            "captured_at": datetime.now(timezone.utc).isoformat()}


def fetch_match_flow(home: str, away: str,
                     asof: datetime | None = None) -> dict:
    """一站式：找赛事 → 提快照。"""
    event = find_match_event(home, away, asof=asof)
    if not event:
        return {"status": "missing", "reason": "Polymarket 无覆盖",
                "home": home, "away": away}
    full = get_event(event.get("slug") or "")
    result = extract_flow(full or event, home, away)
    result.update({"home": home, "away": away,
                   "event_title": event.get("title")})
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Polymarket 赛前资金流快照（只读）")
    ap.add_argument("--home", required=True)
    ap.add_argument("--away", required=True)
    ap.add_argument("--asof", default=None,
                    help="目标时间 ISO（如 2026-10-05），默认现在")
    args = ap.parse_args()
    asof = None
    if args.asof:
        asof = datetime.fromisoformat(args.asof)
        if asof.tzinfo is None:
            asof = asof.replace(tzinfo=timezone.utc)
    print(json.dumps(fetch_match_flow(args.home, args.away, asof=asof),
                     ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
