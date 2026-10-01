"""
The Odds API 接入
https://the-odds-api.com/

免费档: 每月500次请求 (按比赛场次计费, 非按API调用)
用途: 多家博彩公司实时赔率 (h2h/让球/大小球)
Key 存储: ~/.odds_key (权限600, 不进git)
"""

import json
import os
import subprocess

BASE_URL = "https://api.the-odds-api.com/v4"
KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "..", ".odds_key")
KEY_FILE = os.path.abspath(KEY_FILE)

# 足球联赛 key 映射
SOCCER_LEAGUES = {
    "英超": "soccer_epl",
    "英冠": "soccer_efl_champ",
    "英甲": "soccer_england_league1",
    "英乙": "soccer_england_league2",
    "西甲": "soccer_spain_la_liga",
    "西乙": "soccer_spain_segunda_division",
    "意甲": "soccer_italy_serie_a",
    "意乙": "soccer_italy_serie_b",
    "德甲": "soccer_germany_bundesliga",
    "德乙": "soccer_germany_bundesliga2",
    "法甲": "soccer_france_ligue_one",
    "法乙": "soccer_france_ligue_two",
    "荷甲": "soccer_netherlands_eredivisie",
    "葡超": "soccer_portugal_primeira_liga",
    "巴西甲": "soccer_brazil_campeonato",
    "巴西乙": "soccer_brazil_serie_b",
    "美职": "soccer_usa_mls",
    "墨联": "soccer_mexico_ligamx",
    "欧冠": "soccer_uefa_champs_league",
    "欧联": "soccer_uefa_europa_league",
}


def _get_key():
    if not os.path.exists(KEY_FILE):
        raise RuntimeError("The Odds API key 不存在")
    with open(KEY_FILE) as f:
        return f.read().strip()


def _request(endpoint, params=None):
    key = _get_key()
    params = params or {}
    params["apiKey"] = key
    url = f"{BASE_URL}{endpoint}"
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{url}?{qs}"
    cmd = [
        "curl", "-s", "--max-time", "20",
        "--cacert", "/run/hatch/egress-tls/ca-bundle.pem",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    return json.loads(result.stdout)


def get_odds(sport_key, markets="h2h", regions="eu", odds_format="decimal"):
    """
    获取某联赛的实时赔率
    sport_key: 如 "soccer_epl"
    markets: h2h (胜平负), spreads (让球), totals (大小球), 可逗号组合
    regions: eu (欧洲), uk, us, au
    返回: list of {match_id, home, away, commence_time, bookmakers: {name: {h2h: [主,平,客], ...}}}
    """
    params = {
        "markets": markets,
        "regions": regions,
        "oddsFormat": odds_format,
        "dateFormat": "iso",
    }
    data = _request(f"/sports/{sport_key}/odds", params)
    out = []
    for m in data:
        bookmakers = {}
        for bm in m.get("bookmakers", []):
            markets_d = {}
            for mk in bm.get("markets", []):
                key = mk.get("key")
                outcomes = {o["name"]: o.get("price") for o in mk.get("outcomes", [])}
                markets_d[key] = outcomes
            bookmakers[bm.get("title", "")] = markets_d
        out.append({
            "match_id": m.get("id"),
            "home": m.get("home_team", ""),
            "away": m.get("away_team", ""),
            "commence_time": m.get("commence_time", ""),
            "bookmakers": bookmakers,
        })
    return out


def get_quota():
    """查看剩余额度 (从响应头获取，这里用 /sports 调用估算)"""
    # The Odds API 在响应头 x-requests-remaining 返回剩余额度
    key = _get_key()
    cmd = [
        "curl", "-s", "-D", "-", "-o", "/dev/null", "--max-time", "15",
        "--cacert", "/run/hatch/egress-tls/ca-bundle.pem",
        f"{BASE_URL}/sports/?apiKey={key}",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=20)
    headers = result.stdout.decode()
    remaining = None
    used = None
    for line in headers.split("\n"):
        ll = line.lower()
        if "x-requests-remaining" in ll:
            remaining = line.split(":")[1].strip()
        if "x-requests-used" in ll:
            used = line.split(":")[1].strip()
    return {"used": used, "remaining": remaining}


if __name__ == "__main__":
    print("=== The Odds API 接入测试 ===")
    q = get_quota()
    print(f"本月已用: {q['used']}, 剩余: {q['remaining']}")

    print("\n--- 英超实时赔率 (h2h, 欧洲区) ---")
    odds = get_odds("soccer_epl", markets="h2h", regions="eu")
    print(f"共 {len(odds)} 场有赔率")
    for o in odds[:3]:
        print(f"  {o['home']} vs {o['away']} ({o['commence_time'][:16]})")
        for bm_name, mkts in list(o["bookmakers"].items())[:2]:
            h2h = mkts.get("h2h", {})
            print(f"    {bm_name}: 主{h2h.get(o['home'])} 平{h2h.get('Draw')} 客{h2h.get(o['away'])}")
