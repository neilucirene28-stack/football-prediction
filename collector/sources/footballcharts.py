"""
Football-Charts 数据源接入
https://www.football-charts.com - 93个联赛免费数据

提供: 赛果(results)、赛程(fixtures)、积分榜(table)
覆盖咱们缺的: J2 (japan2)、韩K2 (korea2)、英乙 (eng2) 等低级别联赛

API Key: 免费 tier, 5000次/天 (keyless 300次/天)
Key 通过环境变量 FC_API_KEY 传入，不硬编码

注意: 免费 tier 只覆盖当前+上赛季 (如 2026-2027, 2025-2026)，
更早赛季需付费。J2/K2 等咱们之前完全缺的联赛，近两季数据已够用。
"""

import os
import re
import time
from datetime import datetime, timezone

from _http import fetch_with_retry, HTTPError

BASE_URL = "https://footballcharts-backend.onrender.com/api/v1"
ATTRIBUTION = "Data by football-charts.com"

# 联赛代码映射: 咱们内部名 -> football-charts code
LEAGUE_CODES = {
    # 日本
    "J1": "japan1",
    "J2": "japan2",
    # 韩国
    "K1": "korea1",
    "K2": "korea2",
    # 英格兰
    "英超": "premier",
    "英冠": "cha",
    "英甲": "eng1",
    "英乙": "eng2",
    # 巴西
    "巴西甲": "brazil1",
    "巴西乙": "brazil2",
    # 中国
    "中超": "china",
    # 西班牙/德国/意大利/法国 (如有需要再加)
}

# 请求间隔 (秒)，免费 tier 60次/分钟，保守一点
REQUEST_INTERVAL = 1.2
_last_request = 0


def _get_api_key():
    key = os.environ.get("FC_API_KEY", "")
    if not key:
        # 尝试从项目配置文件读取
        cfg_path = os.path.join(os.path.dirname(__file__), "..", "..", ".fc_key")
        cfg_path = os.path.abspath(cfg_path)
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                key = f.read().strip()
    return key


def _request(path):
    """带限流的 GET 请求（重试/退避由 _http.fetch_with_retry 统一处理）"""
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)

    url = f"{BASE_URL}{path}"
    key = _get_api_key()
    headers = {
        "User-Agent": "football-prediction-v2/1.0",
        "Accept": "application/json",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        return fetch_with_retry(url, headers=headers, timeout=30, max_retries=3)
    finally:
        _last_request = time.time()


def parse_held_seasons(error_message):
    """
    从 unknown_season 错误里解析 API 实际持有的赛季列表。
    错误形如: '"japan2" has no season "2026". ... Seasons held: 2025, 2024, 2023, ...'
    返回: ["2025", "2024", ...]（无匹配时返回 []）
    """
    m = re.search(r"[Ss]easons held:\s*([\d,\s]+)", error_message or "")
    if not m:
        return []
    return [s.strip() for s in m.group(1).split(",") if s.strip().isdigit()]


def is_unknown_season_error(exc):
    """判断异常是否为 unknown_season（赛季不存在），调用方可据此回退赛季"""
    return "unknown_season" in str(exc)


def get_leagues():
    """返回所有可用联赛列表"""
    data = _request("/leagues/")
    return data.get("leagues", [])


def get_results(league_code, season=None):
    """
    获取某联赛某赛季全部赛果
    league_code: 如 'japan2'
    season: 如 '2025'，不传则用当前赛季
    返回: list of {date, homeTeam, awayTeam, score, ht_result, ...}
    """
    path = f"/leagues/{league_code}/results/"
    if season:
        path += f"?season={season}"
    data = _request(path)
    return data.get("matches", [])


def get_fixtures(league_code, season=None):
    """获取某联赛赛程 (未开赛的比赛)"""
    path = f"/leagues/{league_code}/fixtures/"
    if season:
        path += f"?season={season}"
    data = _request(path)
    return data.get("matches", [])


def get_table(league_code, season=None):
    """获取积分榜"""
    path = f"/leagues/{league_code}/table/"
    if season:
        path += f"?season={season}"
    data = _request(path)
    return data.get("table", [])


def results_to_history(matches):
    """
    把 API 返回的赛果转成咱们内部历史数据格式
    返回: list of {date, home, away, hg, ag, hthg, htag}
    """
    out = []
    for m in matches:
        score = m.get("score", "")
        if not score or ":" not in score:
            continue
        try:
            hg, ag = score.split(":")
            hg, ag = int(hg.strip()), int(ag.strip())
        except ValueError:
            continue

        # 半场比分
        hthg, htag = None, None
        ht = m.get("ht_result", "")
        if ht and ":" in ht:
            try:
                hthg, htag = [int(x.strip()) for x in ht.split(":")]
            except ValueError:
                pass

        out.append({
            "date": m.get("date", ""),
            "home": m.get("homeTeam", ""),
            "away": m.get("awayTeam", ""),
            "hg": hg,
            "ag": ag,
            "hthg": hthg,
            "htag": htag,
            "source": "football-charts",
        })
    # 按日期排序
    out.sort(key=lambda x: x["date"])
    return out


def fetch_league_history(league_code, seasons):
    """
    拉取某联赛多个赛季的历史数据，合并去重
    seasons: 如 ["2023", "2024", "2025"]
    """
    all_matches = []
    seen = set()
    for season in seasons:
        try:
            matches = get_results(league_code, season)
            for m in results_to_history(matches):
                key = (m["date"], m["home"], m["away"])
                if key not in seen:
                    seen.add(key)
                    all_matches.append(m)
        except Exception as e:
            print(f"  ⚠️ {league_code} {season}: {e}")
    all_matches.sort(key=lambda x: x["date"])
    return all_matches


if __name__ == "__main__":
    # 烟雾测试
    print("=== Football-Charts 接入烟雾测试 ===")
    leagues = get_leagues()
    print(f"可用联赛: {len(leagues)} 个")

    print("\n--- J2 2025赛季 ---")
    j2 = fetch_league_history("japan2", ["2025"])
    print(f"  拉取 {len(j2)} 场")
    if j2:
        print(f"  最早: {j2[0]['date']} {j2[0]['home']} {j2[0]['hg']}-{j2[0]['ag']} {j2[0]['away']}")
        print(f"  最晚: {j2[-1]['date']} {j2[-1]['home']} {j2[-1]['hg']}-{j2[-1]['ag']} {j2[-1]['away']}")

    print(f"\n数据来源: {ATTRIBUTION}")
