"""
FootballSoccerAPI.com (FsAPI) — 历史比分档案，走 walk-forward/回填专用。

https://api.footballsoccerapi.com/v1/matches
680K+ 比赛（2012 年起，928 竞赛/158 国），含全场+半场比分、联赛排名、旅途距离。

免费 key 限制（铁律）：
- 50 请求/天（UTC 00:00 重置），60/分钟；数据延迟 1-7 天
- 免费档无 kickoff 价格/best odds/成交量（held_back，需付费档）
→ 只做历史回填与 walk-forward，不进 daily_fetch.py 实时预测链路。

鉴权：经 skill CLI（~/workspace/skills/footballsoccerapi/bin/fsapi）走
Secure Vault custom.footballsoccerapi，本模块不接触原始 key。
每次 CLI 调用计 1 次请求；配额持久化到 data/fsapi_quota.json，
每日上限 45（50 上限留 5 次余量），超限直接拒绝。
"""
import json
import os
import subprocess
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
FSAPI_CLI = os.path.expanduser("~/workspace/skills/footballsoccerapi/bin/fsapi")
QUOTA_FILE = os.path.join(REPO_ROOT, "data", "fsapi_quota.json")
DAILY_CAP = 45  # 50 上限留余量

BASE_URL = "https://api.footballsoccerapi.com"


def _today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_quota():
    try:
        return json.load(open(QUOTA_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_quota(q):
    os.makedirs(os.path.dirname(QUOTA_FILE), exist_ok=True)
    json.dump(q, open(QUOTA_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def quota_used(day=None):
    """当日已用请求数。"""
    return int(_load_quota().get(day or _today_utc(), 0))


def _charge(n=1):
    q = _load_quota()
    day = _today_utc()
    q[day] = int(q.get(day, 0)) + n
    _save_quota(q)
    return q[day]


def _check_quota(n=1):
    used = quota_used()
    if used + n > DAILY_CAP:
        raise RuntimeError(
            f"FsAPI 每日配额将超限：已用 {used}/{DAILY_CAP}，本次需 {n} 次，拒绝执行")


def _cli(*args):
    """调 skill CLI（1 次调用 = 1 次 API 请求），返回解析后的 JSON。"""
    _check_quota(1)
    proc = subprocess.run(
        [FSAPI_CLI, *args], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"fsapi CLI 失败: {proc.stderr.strip()[:300]}")
    _charge(1)
    return json.loads(proc.stdout)


def normalize(row):
    """FsAPI match 行 → 回填通用格式。缺失字段为 None，不编造。"""
    return {
        "match_id": row.get("match_id"),
        "source": "footballsoccerapi",
        "league_id": row.get("league_id"),
        "league": row.get("league_name"),
        "country": row.get("country_name"),
        "season": row.get("season_start_year"),
        "kickoff_utc": row.get("kickoff_utc"),
        "kickoff_date": row.get("kickoff_date"),
        "home": row.get("home_team_name"),
        "away": row.get("away_team_name"),
        "home_goals": row.get("home_goals"),
        "away_goals": row.get("away_goals"),
        "ht_home": row.get("half_time_home_goals"),
        "ht_away": row.get("half_time_away_goals"),
        "result": row.get("full_time_result"),  # home/draw/away
        "home_pos": row.get("home_league_position"),
        "away_pos": row.get("away_league_position"),
        # 免费档无以下字段，显式标 None：
        "kickoff_odds": None,  # held_back（付费档）
        "volume": None,        # held_back（付费档）
    }


def fetch_matches(league_id=None, country=None, season=None,
                  status="finished", limit=500, max_pages=10):
    """
    拉取历史比赛（cursor 翻页）。每次 API 调用计配额。
    返回: (rows[list of normalized], pages_used[int])
    """
    rows, pages, cursor = [], 0, None
    while pages < max_pages:
        args = ["matches", "--status", status, "--limit", str(limit)]
        if league_id:
            args += ["--league-id", league_id]
        if country:
            args += ["--country", country]
        if season:
            args += ["--season", str(season)]
        if cursor:
            args += ["--cursor", cursor]
        payload = _cli(*args)
        pages += 1
        for r in payload.get("data") or []:
            rows.append(normalize(r))
        cursor = (payload.get("meta") or {}).get("next_cursor")
        if not cursor:
            break
    return rows, pages


def fetch_season(league_id, season, out_path=None):
    """
    拉取某联赛某赛季全部已赛场次（回填用），落盘 JSON。
    配额按实际页数消耗；调用前会预检。
    """
    rows, pages = fetch_matches(league_id=league_id, season=season,
                                status="finished")
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        json.dump(rows, open(out_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    return {"league_id": league_id, "season": season,
            "matches": len(rows), "pages": pages,
            "out": out_path, "quota_used_today": quota_used()}


if __name__ == "__main__":
    print("FsAPI 配额（今日 UTC）:", quota_used(), "/", DAILY_CAP)
    print("注意：免费档无 kickoff 价格/成交量，仅比分档案可用于回填。")
