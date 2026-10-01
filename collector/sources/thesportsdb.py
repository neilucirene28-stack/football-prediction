"""
thesportsdb 接入 (免费档)
https://www.thesportsdb.com/api/v1/json/3/...

公共免费 key = "3"
核心价值: 球队跨源ID映射 (idTeam ↔ idESPN ↔ idAPIfootball)
"""

import json
import os

from _http import fetch_with_retry

BASE_URL = "https://www.thesportsdb.com/api/v1/json/3"
CACHE_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "team_id_map.json")
CACHE_FILE = os.path.abspath(CACHE_FILE)


def _request(endpoint):
    url = f"{BASE_URL}/{endpoint}"
    return fetch_with_retry(url, timeout=20)


def search_teams(league_name):
    """
    按联赛名搜索球队
    如 search_teams("English Premier League")
    返回: 球队列表 (含各源ID)
    """
    # URL编码联赛名
    import urllib.parse
    q = urllib.parse.quote(league_name)
    data = _request(f"search_all_teams.php?l={q}")
    teams = []
    for t in data.get("teams", []) or []:
        teams.append({
            "name": t.get("strTeam", ""),
            "thesportsdb_id": t.get("idTeam", ""),
            "espn_id": t.get("idESPN", ""),
            "apifootball_id": t.get("idAPIfootball", ""),
            "country": t.get("strCountry", ""),
        })
    return teams


def get_next_events(team_id):
    """获取某球队未来赛程"""
    data = _request(f"eventsnext.php?id={team_id}")
    events = []
    for e in data.get("events", []) or []:
        events.append({
            "event_id": e.get("idEvent", ""),
            "date": e.get("dateEvent", ""),
            "home": e.get("strHomeTeam", ""),
            "away": e.get("strAwayTeam", ""),
            "league": e.get("strLeague", ""),
        })
    return events


def build_id_map(leagues=None, save=True):
    """
    构建跨源球队ID映射表
    leagues: 联赛名列表，默认五大联赛
    """
    if leagues is None:
        leagues = [
            "English Premier League",
            "Spanish La Liga",
            "Italian Serie A",
            "German Bundesliga",
            "French Ligue 1",
        ]
    id_map = {}
    for lg in leagues:
        try:
            teams = search_teams(lg)
            for t in teams:
                key = t["name"].lower()
                id_map[key] = {
                    "name": t["name"],
                    "thesportsdb_id": t["thesportsdb_id"],
                    "espn_id": t["espn_id"],
                    "apifootball_id": t["apifootball_id"],
                }
            print(f"  {lg}: {len(teams)}队")
        except Exception as e:
            print(f"  {lg} 失败: {e}")

    if save:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(id_map, f, ensure_ascii=False, indent=1)
        print(f"映射表已保存: {CACHE_FILE} ({len(id_map)}队)")

    return id_map


def lookup(team_name):
    """查某球队的跨源ID"""
    if not os.path.exists(CACHE_FILE):
        return None
    with open(CACHE_FILE, encoding="utf-8") as f:
        id_map = json.load(f)
    return id_map.get(team_name.lower())


if __name__ == "__main__":
    print("=== thesportsdb 测试 ===")
    teams = search_teams("English Premier League")
    print(f"英超: {len(teams)}队")
    if teams:
        t = teams[0]
        print(f"例: {t['name']} ESPN_ID={t['espn_id']} APIFootball_ID={t['apifootball_id']}")
    print("\n构建五大联赛ID映射表...")
    build_id_map()
