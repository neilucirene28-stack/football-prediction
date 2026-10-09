"""
ESPN 隐藏 API 接入
https://site.api.espn.com/apis/site/v2/sports/soccer/{联赛代码}/scoreboard
https://site.api.espn.com/apis/site/v2/sports/soccer/{联赛代码}/summary?event={event_id}

完全免费，无需key，无严格速率限制
注意: 需 --compressed 解 gzip，否则部分联赛返回乱码

summary 端点实测说明 (2026-10-09, 巴西甲 event 401841169):
- 限流: 未触发。连续 12 次请求 summary 全部 200，无 429。
  （_http.fetch_with_retry 仍带 429→等60秒重试逻辑，留作兜底）
- 联赛可用性: summary 端点与 scoreboard 共用同一套 LEAGUE_CODES，
  实测巴西甲可用；结构是 ESPN 全站统一格式，其他联赛同构可用。
- 阵容 (rosters): 完场/进行中场次返回 22-23 人名单，starter=True 为
  11 人首发（含号码/位置/球员级进球/射门等 stats）；赛前未公布阵容时
  rosters 条目存在但 roster 为空列表（开球前约1小时才会放出）。
- 事件 (keyEvents): 进球/点球/红黄牌/换人/上下半场哨，含分钟与文本描述；
  另夹杂 Kickoff/Start Delay/End Delay 等哨声事件，消费端按 type 过滤。
- 技术统计 (boxscore.teams[].statistics): 射门/射正/控球/角球/犯规/
  黄红牌/传球/抢断/拦截等约 29 项，开球后才有，赛前为空列表。
- 备注: boxscore.statistics（顶层）为空，统计在 boxscore.teams 下；
  国家队赛事 (kor.1/jpn.2 等) scoreboard 本就不覆盖，summary 亦不可用。
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


def _parse_player(entry):
    """解析 rosters 下单个球员条目（内部函数，便于单测不走网络）"""
    ath = entry.get("athlete") or {}
    pos = entry.get("position") or {}
    return {
        "name": ath.get("displayName", ""),
        "short_name": ath.get("shortName", ""),
        "jersey": entry.get("jersey", ""),
        "position": pos.get("abbreviation", ""),
        "position_name": pos.get("displayName", ""),
        "starter": bool(entry.get("starter")),
        "formation_place": entry.get("formationPlace"),
        "stats": {s.get("name"): s.get("displayValue")
                  for s in (entry.get("stats") or []) if s.get("name")},
    }


def _parse_summary(data):
    """解析 summary 响应为结构化 dict（内部函数，便于单测不走网络）"""
    comp = (data.get("header", {}).get("competitions") or [{}])[0]
    competitors = comp.get("competitors", [])
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    home_name = home.get("team", {}).get("displayName", "")
    away_name = away.get("team", {}).get("displayName", "")
    # rosters 按队名匹配（顺序通常已对应主客队，名字匹配更稳）
    team_map = {t.get("team", {}).get("displayName", ""): t
                for t in data.get("rosters", [])}
    lineups = {"home": [], "away": []}
    for key, name in (("home", home_name), ("away", away_name)):
        ros = team_map.get(name) or {}
        lineups[key] = [_parse_player(p) for p in (ros.get("roster") or [])]

    events = []
    for e in data.get("keyEvents", []):
        clock = e.get("clock") or {}
        etype = e.get("type") or {}
        events.append({
            "minute": clock.get("displayValue", ""),
            "type": etype.get("text", ""),
            "text": e.get("text") or "",
        })

    stats = {"home": {}, "away": {}}
    for t in data.get("boxscore", {}).get("teams", []):
        side = t.get("homeAway")
        if side in stats:
            stats[side] = {s.get("name"): s.get("displayValue")
                           for s in (t.get("statistics") or []) if s.get("name")}

    return {
        "event_id": comp.get("id", ""),
        "date": comp.get("date", ""),
        "status": comp.get("status", {}).get("type", {}).get("shortDetail", ""),
        "home": home_name,
        "away": away_name,
        "score_home": home.get("score", ""),
        "score_away": away.get("score", ""),
        "lineups": lineups,
        "events": events,
        "stats": stats,
    }


def get_summary(event_id, league_code):
    """
    获取单场比赛详情：阵容 / 关键事件 / 技术统计。
    event_id: get_scoreboard 返回的 event_id（如 "401841169"）
    league_code: 如 "eng.1" 或中文名 "英超"
    返回 dict: {event_id, date, status, home, away, score_home, score_away,
               lineups: {"home": [球员...], "away": [...]},
               events: [{minute, type, text}...],
               stats: {"home": {指标名: 值}, "away": {...}}}
    球员字段: name / short_name / jersey / position(缩写如 G) /
              position_name / starter / formation_place / stats
    事件 type 如 "Goal"/"Penalty - Scored"/"Yellow Card"/"Red Card"/
              "Substitution"，消费端按需过滤。
    赛前阵容未公布时 lineups 为空列表；技术统计只在开球后有。
    """
    code = LEAGUE_CODES.get(league_code, league_code)
    data = _request(f"/{code}/summary", {"event": event_id})
    return _parse_summary(data)


def get_lineups(event_id, league_code):
    """
    快捷函数：只返回阵容 {"home": [球员...], "away": [球员...]}。
    赛前未公布阵容时返回空列表（开球前约1小时放出）。
    """
    return get_summary(event_id, league_code)["lineups"]


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
