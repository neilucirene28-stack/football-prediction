"""
API-Football (api-sports.io) 接入
https://www.api-football.com/

免费档: 100次/天
用途: 实时比分、赛程、积分榜、球队信息
Key 存储: ~/.af_key (权限600, 不进git)
"""

import json
import os
import subprocess

BASE_URL = "https://v3.football.api-sports.io"
KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "..", ".af_key")
KEY_FILE = os.path.abspath(KEY_FILE)

# 常用联赛 ID (API-Football)
LEAGUE_IDS = {
    "英超": 39,
    "西甲": 140,
    "意甲": 135,
    "德甲": 78,
    "法甲": 61,
    "英冠": 40,
    "荷甲": 88,
    "葡超": 94,
    "J1": 98,
    "J2": 99,
    "韩K1": 292,
    "韩K2": 293,
    "中超": 169,
    "巴西甲": 71,
    "巴西乙": 72,
    "美职": 253,
    "墨联": 262,
}


def _get_key():
    if not os.path.exists(KEY_FILE):
        raise RuntimeError("API-Football key 不存在，请先申请并保存到 .af_key")
    with open(KEY_FILE) as f:
        return f.read().strip()


def _request(endpoint, params=None):
    """调用 API-Football"""
    key = _get_key()
    url = f"{BASE_URL}{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"

    cmd = [
        "curl", "-s", "--max-time", "20",
        "--cacert", "/run/hatch/egress-tls/ca-bundle.pem",
        "-H", f"x-apisports-key: {key}",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    data = json.loads(result.stdout)
    if data.get("errors"):
        raise RuntimeError(f"API 错误: {data['errors']}")
    return data


def get_status():
    """查看账户状态和剩余额度"""
    data = _request("/status")
    resp = data.get("response", {})
    return {
        "account": resp.get("account", {}).get("firstname", ""),
        "requests_used": resp.get("requests", {}).get("current", 0),
        "requests_limit": resp.get("requests", {}).get("limit_day", 100),
    }


def get_fixtures(date_str, league_id=None):
    """
    按日期获取赛程
    date_str: "2026-10-02"
    league_id: 可选，限定联赛
    返回: list of {fixture_id, home, away, kickoff, status, goals}
    """
    params = {"date": date_str}
    if league_id:
        params["league"] = league_id
    data = _request("/fixtures", params)
    out = []
    for item in data.get("response", []):
        fx = item.get("fixture", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        out.append({
            "fixture_id": fx.get("id"),
            "date": fx.get("date", ""),
            "status": fx.get("status", {}).get("short", ""),
            "home": teams.get("home", {}).get("name", ""),
            "away": teams.get("away", {}).get("name", ""),
            "home_id": teams.get("home", {}).get("id"),
            "away_id": teams.get("away", {}).get("id"),
            "goals_home": goals.get("home"),
            "goals_away": goals.get("away"),
            "league": item.get("league", {}).get("name", ""),
        })
    return out


def get_live_scores():
    """获取正在进行的比赛"""
    data = _request("/fixtures", {"live": "all"})
    out = []
    for item in data.get("response", []):
        fx = item.get("fixture", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        out.append({
            "fixture_id": fx.get("id"),
            "status": fx.get("status", {}).get("short", ""),
            "elapsed": fx.get("status", {}).get("elapsed"),
            "home": teams.get("home", {}).get("name", ""),
            "away": teams.get("away", {}).get("name", ""),
            "goals_home": goals.get("home"),
            "goals_away": goals.get("away"),
            "league": item.get("league", {}).get("name", ""),
        })
    return out


def get_standings(league_id, season):
    """
    获取积分榜
    league_id: 如 39 (英超)
    season: 如 2026
    """
    data = _request("/standings", {"league": league_id, "season": season})
    resp = data.get("response", [])
    if not resp:
        return []
    standings = resp[0].get("league", {}).get("standings", [[]])[0]
    out = []
    for s in standings:
        out.append({
            "rank": s.get("rank"),
            "team": s.get("team", {}).get("name", ""),
            "played": s.get("all", {}).get("played", 0),
            "win": s.get("all", {}).get("win", 0),
            "draw": s.get("all", {}).get("draw", 0),
            "lose": s.get("all", {}).get("lose", 0),
            "gf": s.get("all", {}).get("goals", {}).get("for", 0),
            "ga": s.get("all", {}).get("goals", {}).get("against", 0),
            "points": s.get("points", 0),
        })
    return out


if __name__ == "__main__":
    print("=== API-Football 接入测试 ===")
    st = get_status()
    print(f"账户: {st['account']}, 今日已用: {st['requests_used']}/{st['requests_limit']}")

    print("\n--- 明日赛程 (2026-10-02) ---")
    fixtures = get_fixtures("2026-10-02")
    print(f"共 {len(fixtures)} 场")
    for f in fixtures[:5]:
        print(f"  {f['league']}: {f['home']} vs {f['away']}")

    st2 = get_status()
    print(f"\n本次消耗: {st2['requests_used'] - st['requests_used']} 次")
