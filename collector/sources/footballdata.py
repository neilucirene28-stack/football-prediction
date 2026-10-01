"""
football-data.org API 接入
https://www.football-data.org/

免费档: 10次/分钟
用途: 赛程、积分榜、射手榜
Key 存储: ~/.fd_key (权限600, 不进git)
"""

import json
import os
import subprocess

BASE_URL = "https://api.football-data.org/v4"
KEY_FILE = os.path.join(os.path.dirname(__file__), "..", "..", ".fd_key")
KEY_FILE = os.path.abspath(KEY_FILE)

# 联赛代码
COMPETITIONS = {
    "英超": "PL",
    "英冠": "ELC",
    "西甲": "PD",
    "意甲": "SA",
    "德甲": "BL1",
    "法甲": "FL1",
    "荷甲": "DED",
    "葡超": "PPL",
    "巴西甲": "BSA",
    "欧冠": "CL",
}


def _get_key():
    if not os.path.exists(KEY_FILE):
        raise RuntimeError("football-data.org key 不存在")
    with open(KEY_FILE) as f:
        return f.read().strip()


def _request(endpoint, params=None):
    key = _get_key()
    url = f"{BASE_URL}{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    cmd = [
        "curl", "-s", "--max-time", "20",
        "--cacert", "/run/hatch/egress-tls/ca-bundle.pem",
        "-H", f"X-Auth-Token: {key}",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)
    return json.loads(result.stdout)


def get_matches(date_from, date_to, competitions=None):
    """
    按日期范围获取比赛
    date_from/date_to: "2026-10-02"
    competitions: 如 "PL,PD" (逗号分隔) 或 None 查全部
    """
    params = {"dateFrom": date_from, "dateTo": date_to}
    if competitions:
        params["competitions"] = competitions
    data = _request("/matches", params)
    out = []
    for m in data.get("matches", []):
        out.append({
            "match_id": m.get("id"),
            "utc_date": m.get("utcDate", ""),
            "status": m.get("status", ""),
            "home": m.get("homeTeam", {}).get("name", ""),
            "away": m.get("awayTeam", {}).get("name", ""),
            "score_home": (m.get("score", {}).get("fullTime", {}) or {}).get("home"),
            "score_away": (m.get("score", {}).get("fullTime", {}) or {}).get("away"),
            "competition": m.get("competition", {}).get("name", ""),
            "competition_code": m.get("competition", {}).get("code", ""),
        })
    return out


def get_standings(competition_code):
    """获取积分榜，如 get_standings('PL')"""
    data = _request(f"/competitions/{competition_code}/standings")
    out = []
    for table in data.get("standings", []):
        if table.get("type") != "TOTAL":
            continue
        for s in table.get("table", []):
            out.append({
                "position": s.get("position"),
                "team": s.get("team", {}).get("name", ""),
                "played": s.get("playedGames", 0),
                "won": s.get("won", 0),
                "draw": s.get("draw", 0),
                "lost": s.get("lost", 0),
                "gf": s.get("goalsFor", 0),
                "ga": s.get("goalsAgainst", 0),
                "points": s.get("points", 0),
            })
    return out


if __name__ == "__main__":
    print("=== football-data.org 接入测试 ===")
    matches = get_matches("2026-10-02", "2026-10-03")
    print(f"10-02~10-03 比赛: {len(matches)} 场")
    for m in matches[:5]:
        print(f"  {m['competition_code']} {m['home']} vs {m['away']}")

    print("\n--- 英超积分榜 Top5 ---")
    for s in get_standings("PL")[:5]:
        print(f"  {s['position']}. {s['team']} {s['points']}分")
