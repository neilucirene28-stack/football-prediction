"""
soccerdata 库接入 (pip install soccerdata)

注意: 本文件名 soccerdata.py 会遮蔽同名 pip 包, 因此用 importlib 按
site-packages 路径把真正的包加载为私有别名 `_soccerdata_real`
(pip 包内部用相对导入, 别名加载不受影响)。

子模块实测结论 (2026-10-09, 本机机房 IP):
- ClubElo: 库可导入, 但 api.clubelo.com 走出口代理返回 502,
  本机不可用 (与 2026-10-01 调研 "ClubElo 被墙" 一致)。
  get_club_elo() 已实现, 放阿里云直连可再测, 本机调会抛异常。
- Understat: ✅ 可用。soccerdata 1.9.1 已适配 2025 年底改版的新 API
  (read_leagues / read_seasons / read_team_match_stats 实测通过)。
  覆盖 5 联赛: 英超 / 西甲 / 意甲 / 德甲 / 法甲。
- FBref: ❌ 本机不可用。soccerdata 1.9.1 的 FBref 需要 Chrome
  (seleniumbase undetected), 本机没有, 暂不接。
- WhoScored: Cloudflare 拦截机房 IP, 暂不接。
- ESPN: 项目已有 espn.py 直调隐藏 API, 不走 soccerdata。

缓存: soccerdata 自带本地缓存, 目录由环境变量 SOCCERDATA_DIR 控制,
默认 ~/soccerdata, 数据在 ~/soccerdata/data/<源名>/ (如 data/Understat)。
daily_fetch 侧按天刷新: 调用时传 refresh=True (底层 no_cache=True,
强制重新下载并覆盖缓存)。也可以手动删 ~/soccerdata/data/Understat
或设环境变量 SOCCERDATA_MAXAGE (秒) 控制缓存有效期。

代理: 构造函数 proxy 参数从环境变量读取, 优先级
SOCCERDATA_PROXY > HTTPS_PROXY > HTTP_PROXY。本机出口需走代理,
否则 Understat 也会报 TLS 握手失败。
"""

import importlib.util
import os
import sys
from pathlib import Path


def _load_real_soccerdata():
    """按 site-packages 路径加载真正的 soccerdata pip 包(避开本模块同名遮蔽)。"""
    real = sys.modules.get("_soccerdata_real")
    if real is not None:
        return real
    here = str(Path(__file__).resolve().parent)
    for entry in sys.path:
        if not entry:
            continue
        try:
            if str(Path(entry).resolve()) == here:
                continue
        except OSError:
            continue
        init = Path(entry) / "soccerdata" / "__init__.py"
        if init.is_file():
            spec = importlib.util.spec_from_file_location(
                "_soccerdata_real",
                init,
                submodule_search_locations=[str(init.parent)],
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules["_soccerdata_real"] = mod
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("找不到 pip 安装的 soccerdata 包, 请先 pip install soccerdata")


_SD = _load_real_soccerdata()
ClubElo = _SD.ClubElo
Understat = _SD.Understat

# 中文名 -> soccerdata 联赛名 (Understat/ClubElo 目前都只覆盖这 5 个)
LEAGUES = {
    "英超": "ENG-Premier League", "EPL": "ENG-Premier League",
    "西甲": "ESP-La Liga", "La_liga": "ESP-La Liga",
    "意甲": "ITA-Serie A", "Serie_A": "ITA-Serie A",
    "德甲": "GER-Bundesliga", "Bundesliga": "GER-Bundesliga",
    "法甲": "FRA-Ligue 1", "Ligue_1": "FRA-Ligue 1",
}


def _get_proxy():
    """代理地址, 优先级 SOCCERDATA_PROXY > HTTPS_PROXY > HTTP_PROXY。"""
    return (
        os.environ.get("SOCCERDATA_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
    )


def _canonical_league(league):
    """中文名/别名 -> soccerdata 联赛名, 不支持则抛 ValueError。"""
    if league in LEAGUES:
        return LEAGUES[league]
    if league in LEAGUES.values():
        return league
    raise ValueError(f"不支持的联赛: {league}, 可选: {sorted(set(LEAGUES.values()))}")


def _normalize_season(season):
    """赛季归一化: "2025" / 2025 / "2025/26" / "2526" / "25/26" -> "2025"。"""
    s = str(season).strip().replace("-", "/")
    if "/" in s:
        s = s.split("/")[0]
    if len(s) == 4 and s.isdigit():
        if s.startswith(("19", "20")):
            return s  # "2025" 年份
        return "20" + s[:2]  # "2526" 赛季码 -> "2025"
    if len(s) == 2 and s.isdigit():  # "25" -> "2025"
        return "20" + s
    raise ValueError(f"无法解析赛季: {season}")


def get_club_elo(league=None, refresh=False):
    """
    获取 ClubElo 全球俱乐部 Elo 评分。
    league: 中文名如 "英超", 不传返回全部球队。
    refresh: True 则强制重新下载(按天刷新用)。
    返回: [{team, elo, rank, league}], 按 rank 升序。
    失败抛异常 (网络不通等), 不吞。
    """
    elo = ClubElo(proxy=_get_proxy(), no_cache=refresh)
    df = elo.read_by_date()
    if league is not None:
        canon = _canonical_league(league)
        df = df[df["league"] == canon]
        if df.empty:
            raise ValueError(f"ClubElo 无联赛数据: {canon}")
    out = []
    for team, row in df.sort_values("rank").iterrows():
        out.append({
            "team": team,
            "elo": float(row["elo"]),
            "rank": int(row["rank"]),
            "league": None if row["league"] != row["league"] else row["league"],
        })
    return out


def _aggregate_team_xg(df):
    """把 Understat per-match 队级数据聚合成每队赛季 xG。纯函数, 便于单测。"""
    teams = {}
    for _, r in df.iterrows():
        for prefix in ("home", "away"):
            other = "away" if prefix == "home" else "home"
            team = r[f"{prefix}_team"]
            s = teams.setdefault(team, {
                "team": team, "played": 0, "goals": 0.0, "goals_against": 0.0,
                "xg": 0.0, "xga": 0.0, "points": 0.0,
                "_ppda": 0.0, "_ppda_n": 0, "_deep": 0.0, "_deep_n": 0,
            })
            s["played"] += 1
            s["xg"] += float(r[f"{prefix}_xg"])
            s["xga"] += float(r[f"{other}_xg"])
            s["goals"] += float(r[f"{prefix}_goals"])
            s["goals_against"] += float(r[f"{other}_goals"])
            s["points"] += float(r[f"{prefix}_points"])
            ppda = r[f"{prefix}_ppda"]
            if ppda == ppda:  # 非 NaN
                s["_ppda"] += float(ppda)
                s["_ppda_n"] += 1
            deep = r[f"{prefix}_deep_completions"]
            if deep == deep:
                s["_deep"] += float(deep)
                s["_deep_n"] += 1
    out = []
    for team in sorted(teams):
        s = teams[team]
        p = s["played"]
        out.append({
            "team": team,
            "played": p,
            "goals": s["goals"],
            "goals_against": s["goals_against"],
            "xg": round(s["xg"], 3),
            "xga": round(s["xga"], 3),
            "xg_per_match": round(s["xg"] / p, 3) if p else 0.0,
            "xga_per_match": round(s["xga"] / p, 3) if p else 0.0,
            "points": s["points"],
            "ppda": round(s["_ppda"] / s["_ppda_n"], 2) if s["_ppda_n"] else None,
            "deep_completions": round(s["_deep"] / s["_deep_n"], 2) if s["_deep_n"] else None,
        })
    return out


def get_understat_xg(league, season="2025", refresh=False):
    """
    获取 Understat 某联赛某赛季每队 xG 聚合。
    league: 中文名如 "英超" (仅 5 联赛: 英超/西甲/意甲/德甲/法甲)。
    season: "2025" / "2025/26" / "2526" (2025-26 赛季)。
    refresh: True 则强制重新下载(按天刷新用)。
    返回: [{team, played, goals, goals_against, xg, xga,
            xg_per_match, xga_per_match, points, ppda, deep_completions}]。
    失败抛异常, 不吞。
    """
    canon = _canonical_league(league)
    season_id = _normalize_season(season)
    u = Understat(leagues=canon, seasons=season_id, proxy=_get_proxy(),
                  no_cache=refresh)
    df = u.read_team_match_stats()
    return _aggregate_team_xg(df)


if __name__ == "__main__":
    print("=== soccerdata 接入自测 ===")
    try:
        xg = get_understat_xg("英超", "2025")
        print(f"Understat 英超 2025: {len(xg)} 队")
        for t in xg[:3]:
            print(f"  {t['team']}: xg/match={t['xg_per_match']} xga/match={t['xga_per_match']} played={t['played']}")
    except Exception as e:
        print(f"Understat 失败: {type(e).__name__}: {e}")
    try:
        elo = get_club_elo("英超")
        print(f"ClubElo 英超: {len(elo)} 队, 头名 {elo[0]['team']} elo={elo[0]['elo']:.0f}")
    except Exception as e:
        print(f"ClubElo 失败(本机预期被墙): {type(e).__name__}: {e}")
