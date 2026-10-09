"""
OddsPapi 接入 — 350+ 博彩公司赔率聚合
https://oddspapi.io/ （v4 REST API，官方文档 2026-10-09 实测确认）

【免费档】每月 250 次请求：
- 1 请求 = 1 次计费端点调用，不按场次/盘口/博彩公司数计费；
  /v4/odds 一次返回整场 130+ 家博彩公司、50~100+ 个盘口（全量市场）
- 历史赔率 /v4/historical-odds **永远免费、不计配额**（单次最多 3 家 bookmaker）
- 配额查询 /v4/account **不计配额**，超限后仍可调用
- WebSocket 只有 Pro 档有，免费档用 REST 轮询

【注册拿 key】
1. 打开 https://oddspapi.io/ 免费注册（无需信用卡）
2. 在 Dashboard 复制 API key
3. 通过 Muse Secure Vault 授权（custom.oddspapi），由授权方把 key 注入环境变量 ODDSPAPI_KEY
本模块只从环境变量 ODDSPAPI_KEY 读 key：不写死、不落文件、不进 git。

【端点】
GET /v4/sports                         → [{sportId, sportName}]（足球 sportId=10）
GET /v4/fixtures?sportId&from&to[&hasOdds] → 赛程（fixtureId/participant1Name/participant2Name/tournamentName/startTime/hasOdds）
GET /v4/odds?fixtureId[&bookmakers]    → 全博彩公司即时赔率（1 请求/场）
GET /v4/historical-odds?fixtureId[&bookmakers] → 历史赔率快照（免费，不计配额）
GET /v4/account                        → 订阅/配额状态（免费，不计配额）

【鉴权】apiKey 放在 query 参数里，**不要放 header**。

【赔率 JSON 结构】
bookmakerOdds[slug].markets[marketId].outcomes[outcomeId].players["0"].price
常用市场 ID：101=胜平负，104=双方都进球(BTTS)，1010=大小球2.5，
1068=亚盘-0.5，1072=亚盘0，1076=亚盘+0.5
胜平负 outcome：101=主胜，102=平，103=客胜

【配额】
data/oddspapi_quota.json 按自然月（UTC）计数；每月上限 240（官方 250，留 10 次余量）；
调用计费端点前先检查，超限抛异常不请求。免费端点（historical-odds/account）不计数。

【与 The Odds API 的互补关系】
The Odds API：500 credits/月，但按“市场数×地区”扣费（一次多市场调用可达 15+ credits），
历史赔率 10 倍扣费。OddsPapi：250 请求/月，一次调用全盘口全博彩公司，
历史赔率免费。OddsPapi 主攻多家博彩公司全盘口快照（含 Pinnacle/SBOBet 等 sharp）+ 免费历史回填。
"""
import json
import os
from datetime import datetime, timedelta, timezone

from _http import fetch_with_retry

BASE_URL = "https://api.oddspapi.io/v4"

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
QUOTA_FILE = os.path.join(REPO_ROOT, "data", "oddspapi_quota.json")
MONTHLY_CAP = 240  # 官方 250/月，留 10 次余量

SOCCER_SPORT_ID = 10

# 常用市场 ID（官方文档）
MARKET_IDS = {
    "1x2": "101",        # 胜平负
    "btts": "104",       # 双方都进球
    "ou25": "1010",      # 大小球 2.5
    "ah_minus_05": "1068",  # 亚盘 -0.5
    "ah_0": "1072",      # 亚盘 0
    "ah_plus_05": "1076",   # 亚盘 +0.5
}
OUTCOME_1X2 = {"101": "home", "102": "draw", "103": "away"}


# ---------------- key 管理 ----------------
def _get_key():
    key = os.environ.get("ODDSPAPI_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ODDSPAPI_KEY 环境变量未设置。注册流程：\n"
            "1) 打开 https://oddspapi.io/ 免费注册（无需信用卡）\n"
            "2) 在 Dashboard 复制 API key\n"
            "3) 通过 Muse Secure Vault 授权（custom.oddspapi），由授权方把 key 注入 ODDSPAPI_KEY\n"
            "本模块只从环境变量读 key，不接触明文，不写文件，不进 git。"
        )
    return key


# ---------------- 配额计数器（自然月） ----------------
def _today_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load_quota():
    try:
        with open(QUOTA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_quota(q):
    os.makedirs(os.path.dirname(QUOTA_FILE), exist_ok=True)
    with open(QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=1)


def quota_used(month=None):
    """当月已用请求数（本地计数器）。"""
    return int(_load_quota().get(month or _today_month(), 0))


def _charge(n=1):
    q = _load_quota()
    month = _today_month()
    q[month] = int(q.get(month, 0)) + n
    _save_quota(q)
    return q[month]


def _check_quota(n=1):
    used = quota_used()
    if used + n > MONTHLY_CAP:
        raise RuntimeError(
            "OddsPapi 本月配额将超限：已用 %d/%d，本次需 %d 次，拒绝执行"
            % (used, MONTHLY_CAP, n)
        )


# ---------------- 请求封装 ----------------
def _request(endpoint, params=None, billable=True):
    params = dict(params or {})
    params["apiKey"] = _get_key()
    if billable:
        _check_quota(1)
    qs = "&".join("%s=%s" % (k, v) for k, v in params.items())
    data = fetch_with_retry("%s%s?%s" % (BASE_URL, endpoint, qs), timeout=25)
    if billable:
        # 官方口径：请求到达服务端即计费（200/4xx/5xx 都计），网络层失败不计
        _charge(1)
    return data


# ---------------- 解析 ----------------
def _price(outcome):
    """outcome → players["0"].price，缺失返回 None。"""
    try:
        return outcome.get("players", {}).get("0", {}).get("price")
    except AttributeError:
        return None


def parse_odds(data):
    """
    解析 /v4/odds 返回。
    返回 {"fixture_id": ..., "bookmakers": {slug: {market_id: {outcome_id: price}}}}
    """
    bookmakers = {}
    for slug, bm in (data.get("bookmakerOdds") or {}).items():
        markets = {}
        for mid, mk in (bm.get("markets") or {}).items():
            outcomes = {}
            for oid, oc in (mk.get("outcomes") or {}).items():
                p = _price(oc)
                if p is not None:
                    outcomes[str(oid)] = p
            if outcomes:
                markets[str(mid)] = outcomes
        if markets:
            bookmakers[slug] = markets
    return {"fixture_id": data.get("fixtureId"), "bookmakers": bookmakers}


def get_1x2(parsed, slug):
    """取某博彩公司的胜平负三元组 (home, draw, away)，缺失返回 None。"""
    mkts = parsed.get("bookmakers", {}).get(slug, {})
    m = mkts.get(MARKET_IDS["1x2"], {})
    if not all(k in m for k in ("101", "102", "103")):
        return None
    return (m["101"], m["102"], m["103"])


def parse_fixtures(data):
    """解析 /v4/fixtures 返回的列表。"""
    out = []
    for f in data or []:
        out.append({
            "fixture_id": f.get("fixtureId"),
            "home": f.get("participant1Name", ""),
            "away": f.get("participant2Name", ""),
            "tournament": f.get("tournamentName", ""),
            "start_time": f.get("startTime", ""),
            "has_odds": bool(f.get("hasOdds")),
        })
    return out


def parse_historical(data):
    """
    解析 /v4/historical-odds 返回（防御性：官方仅承诺“带时间戳的快照列表”，
    字段名以实测为准）。返回 [{"timestamp": ..., "bookmakers": {...}}]。
    """
    snaps = data
    if isinstance(data, dict):
        snaps = data.get("snapshots") or data.get("data") or []
    out = []
    for s in snaps or []:
        ts = s.get("timestamp") or s.get("changedAt") or s.get("createdAt") or ""
        bookmakers = {}
        for slug, bm in (s.get("bookmakerOdds") or {}).items():
            markets = {}
            for mid, mk in (bm.get("markets") or {}).items():
                outcomes = {}
                for oid, oc in (mk.get("outcomes") or {}).items():
                    p = _price(oc)
                    if p is not None:
                        outcomes[str(oid)] = p
                if outcomes:
                    markets[str(mid)] = outcomes
            if markets:
                bookmakers[slug] = markets
        out.append({"timestamp": ts, "bookmakers": bookmakers})
    return out


# ---------------- 公开 API ----------------
def get_sports():
    """运动项目列表（计费 1 次）。返回 [{"sport_id": 10, "sport_name": "Soccer"}, ...]"""
    data = _request("/sports")
    return [{"sport_id": s.get("sportId"), "sport_name": s.get("sportName")}
            for s in (data or [])]


def get_fixtures(sport_id=SOCCER_SPORT_ID, from_date=None, to_date=None,
                 has_odds=True):
    """
    赛程（计费 1 次）。
    sport_id: 足球=10；from_date/to_date: "YYYY-MM-DD"，默认今天起 3 天。
    """
    now = datetime.now(timezone.utc)
    params = {
        "sportId": sport_id,
        "from": from_date or now.strftime("%Y-%m-%d"),
        "to": to_date or (now + timedelta(days=2)).strftime("%Y-%m-%d"),
    }
    if has_odds:
        params["hasOdds"] = "true"
    return parse_fixtures(_request("/fixtures", params))


def get_odds(fixture_id, bookmakers=None):
    """
    单场全博彩公司即时赔率（计费 1 次）。
    bookmakers: slug 逗号串如 "pinnacle,bet365,sbobet"，None=全部。
    返回 parse_odds 的结构。
    """
    params = {"fixtureId": fixture_id}
    if bookmakers:
        params["bookmakers"] = bookmakers
    return parse_odds(_request("/odds", params))


def get_historical_odds(fixture_id, bookmakers=None):
    """
    单场历史赔率快照（**免费，不计配额**；单次最多 3 家 bookmaker）。
    """
    params = {"fixtureId": fixture_id}
    if bookmakers:
        params["bookmakers"] = bookmakers
    return parse_historical(_request("/historical-odds", params, billable=False))


def get_quota():
    """
    配额状态（**免费，不计配额**，超限后仍可调）。
    返回 {"local_used": 本地计数, "monthly_cap": 上限, "account": /v4/account 原始返回}。
    """
    account = _request("/account", billable=False)
    info = {}
    if isinstance(account, dict):
        info = {k: account.get(k) for k in
                ("requestsUsed", "requestsRemaining", "requestsLimit",
                 "plan", "subscription", "used", "remaining", "limit")
                if k in account}
    return {"local_used": quota_used(), "monthly_cap": MONTHLY_CAP,
            "account": info or account}


if __name__ == "__main__":
    print("=== OddsPapi 接入测试 ===")
    try:
        _get_key()
    except RuntimeError as e:
        print(e)
        raise SystemExit(1)

    q = get_quota()
    print("本月本地已用: %d/%d" % (q["local_used"], q["monthly_cap"]))
    print("服务端配额信息: %s" % (q["account"],))

    print("\n--- 运动项目（前 5）---")
    for s in get_sports()[:5]:
        print("  ID %s: %s" % (s["sport_id"], s["sport_name"]))

    print("\n--- 足球未来 3 天有赔率赛程（前 5）---")
    fxs = get_fixtures()
    print("共 %d 场" % len(fxs))
    for f in fxs[:5]:
        print("  %s vs %s | %s | %s" % (f["home"], f["away"],
                                        f["tournament"], f["start_time"]))

    if fxs:
        fid = fxs[0]["fixture_id"]
        print("\n--- 单场赔率 %s（pinnacle/bet365 胜平负）---" % fid)
        odds = get_odds(fid, bookmakers="pinnacle,bet365")
        for slug in ("pinnacle", "bet365"):
            print("  %s: %s" % (slug, get_1x2(odds, slug)))
