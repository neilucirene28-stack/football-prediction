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

import json
import os
import subprocess
import time
from datetime import datetime, timezone

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
    """带限流的 GET 请求 (经 curl 子进程，走系统代理配置)"""
    global _last_request
    elapsed = time.time() - _last_request
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)

    url = f"{BASE_URL}{path}"
    key = _get_api_key()

    cmd = [
        "curl", "-s", "--max-time", "30",
        "--cacert", "/run/hatch/egress-tls/ca-bundle.pem",
        "-H", "User-Agent: football-prediction-v2/1.0",
        "-H", "Accept: application/json",
    ]
    if key:
        cmd += ["-H", f"Authorization: Bearer {key}"]
    # 429 时把响应头也打出来判断
    cmd += ["-w", "\n%{http_code}", url]

    last_err = None
    for attempt in range(3):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
            _last_request = time.time()
            out = proc.stdout.rstrip("\n")
            # 分离 body 和 status code
            if "\n" in out:
                body, code_str = out.rsplit("\n", 1)
            else:
                body, code_str = out, ""
            try:
                code = int(code_str.strip())
            except ValueError:
                code = 0
            if code == 429:
                time.sleep(60)
                continue
            if code != 200:
                raise RuntimeError(f"football-charts API {code}: {path}: {body[:200]}")
            return json.loads(body)
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"football-charts API 请求失败(3次重试): {path}: {last_err}")


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
