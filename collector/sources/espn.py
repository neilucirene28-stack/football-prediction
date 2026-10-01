"""
ESPN 隐藏 API 接入
https://site.api.espn.com/apis/site/v2/sports/soccer/{联赛代码}/scoreboard

完全免费，无需key，无严格速率限制
注意: 需 --compressed 解 gzip，否则部分联赛返回乱码
"""

from _http import fetch_with_retry

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# 实测可用的联赛代码 (2026-10-01 验证)
LEAGUE_CODES = {
    # 五大联赛
    "英超": "eng.1", "西甲": "esp.1", "意甲": "ita.1",
    "德甲": "ger.1", "法甲": "fra.1",
    # 次级联赛
    "英冠": "eng.2", "德乙": "ger.2", "法乙": "fra.2", "意乙": "ita.2",
    # 荷葡苏
    "荷甲": "ned.1", "葡超": "por.1", "苏超": "sco.1",
    # 美洲
    "美职": "usa.1", "墨联": "mex.1", "巴西甲": "bra.1", "智利甲": "chi.1",
    # 亚洲
    "J1": "jpn.1",
    # 北欧
    "瑞典超": "swe.1", "挪超": "nor.1", "丹超": "den.1", "芬超": "fin.1",
    # 其他
    "澳超": "aus.1",
    # 欧战
    "欧冠": "uefa.champions", "欧联": "uefa.europa",
}

# 不覆盖 (实测400/FAIL): kor.1/kor.2 (韩K), jpn.2/jpn.3 (J2/J3)


def _request(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    # --compressed: ESPN 部分联赛返回 gzip，不解会乱码
    return fetch_with_retry(url, extra_args=["--compressed"], timeout=20)


def get_scoreboard(league_code, date_str=None):
    """
    获取赛程/比分
    league_code: 如 "eng.1" 或中文名 "英超"
    date_str: "20261001" (可选，不传返回最近比赛日)
    """
    code = LEAGUE_CODES.get(league_code, league_code)
    params = {}
    if date_str:
        params["dates"] = date_str
    data = _request(f"/{code}/scoreboard", params)
    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})
        out.append({
            "event_id": ev.get("id"),
            "date": ev.get("date", ""),
            "status": comp.get("status", {}).get("type", {}).get("shortDetail", ""),
            "home": home.get("team", {}).get("displayName", ""),
            "away": away.get("team", {}).get("displayName", ""),
            "home_id": home.get("team", {}).get("id", ""),
            "away_id": away.get("team", {}).get("id", ""),
            "score_home": home.get("score", ""),
            "score_away": away.get("score", ""),
            "league": data.get("leagues", [{}])[0].get("name", ""),
        })
    return out


def get_standings(league_code, season="2026"):
    """获取积分榜"""
    code = LEAGUE_CODES.get(league_code, league_code)
    data = _request(f"/{code}/standings", {"season": season})
    out = []
    for group in data.get("children", []):
        for team in group.get("standings", {}).get("entries", []):
            stats = {s["name"]: s.get("value") for s in team.get("stats", [])}
            out.append({
                "team": team.get("team", {}).get("displayName", ""),
                "team_id": team.get("team", {}).get("id", ""),
                "played": stats.get("gamesPlayed", 0),
                "won": stats.get("wins", 0),
                "draw": stats.get("ties", 0),
                "lost": stats.get("losses", 0),
                "gf": stats.get("pointsFor", 0),
                "ga": stats.get("pointsAgainst", 0),
                "points": stats.get("points", 0),
                "rank": stats.get("rank", ""),
            })
    return out


if __name__ == "__main__":
    print("=== ESPN 隐藏 API 测试 ===")
    for cn in ["英超", "J1", "巴西甲", "美职"]:
        try:
            sb = get_scoreboard(cn)
            print(f"{cn}: {len(sb)}场")
            if sb:
                m = sb[0]
                print(f"  例: {m['home']} vs {m['away']} ({m['status']})")
        except Exception as e:
            print(f"{cn} 失败: {e}")
