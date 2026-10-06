"""
FotMob 非官方 JSON API 接入
https://www.fotmob.com/api/data/...

覆盖 59 个联赛，含北单常客低级别联赛（J2/J3、K2/K3、挪甲/挪乙、
瑞典甲/瑞典乙、巴西乙），补 ESPN 不覆盖的短板。

特点: 免 key、免登录、纯 JSON。
短板: 单场 odds 字段恒为 null（无赔率，本模块不解析赔率）；
      非官方接口，随时可能变更——调用方必须做异常隔离，
      失败只记 FAIL 不得中断其他源。
抓取纪律: 请求间隔 >=1s（模块内部统一限速）。

端点:
- GET /api/data/matches?date=YYYYMMDD      当日全部 59 联赛赛程/比分
- GET /api/data/leagues?id={id}&ccode3={cc} 联赛: 积分榜 + 全部赛程比分
- GET /api/data/teams?id={team_id}          球队: 近况/赛程/阵容/历史
- GET /api/data/match?id={match_id}         单场: 比分/状态（无赔率）
"""

import time

from _http import fetch_with_retry

BASE_URL = "https://www.fotmob.com/api/data"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "application/json"}
# 抓取纪律: FotMob 非官方接口，请求间隔 >=1s
RATE_LIMIT_SEC = 1.0

# 已实测联赛 ID（2026-10-06 验证，全部 200）
LEAGUES = {
    "J2联赛": (8974, "JPN"),
    "J3联赛": (9136, "JPN"),
    "K2联赛": (9116, "KOR"),
    "K3联赛": (9537, "KOR"),
    "挪甲": (203, "NOR"),
    "挪乙": (204, "NOR"),
    "瑞典甲": (168, "SWE"),
    "瑞典乙": (169, "SWE"),
    "巴西乙": (8814, "BRA"),
}
# 每日管线抓取的低级别联赛名单
FOTMOB_LOW_LEAGUES = list(LEAGUES.keys())


def _request(path, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    try:
        return fetch_with_retry(url, headers=HEADERS, timeout=25)
    except Exception as e:
        # 失败告警: 非官方接口，调用方按源隔离；这里只加上下文
        raise RuntimeError(f"FotMob 请求失败 {path}: {e}") from e
    finally:
        time.sleep(RATE_LIMIT_SEC)


def _parse_scores_str(s):
    """'17-8' -> (gf, ga)；解析失败返回 (None, None)"""
    try:
        gf, ga = s.split("-")
        return int(gf), int(ga)
    except Exception:
        return None, None


def parse_standings(data):
    """纯解析：联赛 leagues 响应 -> 积分榜列表（零网络，可单测）。"""
    tables = data.get("table") or []
    if not tables:
        raise RuntimeError("FotMob: 积分榜为空")
    rows = tables[0]["data"]["table"]["all"]
    out = []
    for r in rows:
        gf, ga = _parse_scores_str(r.get("scoresStr", ""))
        out.append({
            "name": r.get("name", ""),
            "shortName": r.get("shortName", ""),
            "fotmob_id": str(r.get("id", "")),
            "played": r.get("played", 0),
            "wins": r.get("wins", 0),
            "draws": r.get("draws", 0),
            "losses": r.get("losses", 0),
            "gf": gf, "ga": ga,
            "gd": r.get("goalConDiff"),
            "pts": r.get("pts", 0),
            "rank": r.get("idx", 0),
        })
    return out


def get_standings(league_cn):
    """
    联赛积分榜。
    返回 [{name, shortName, fotmob_id, played, wins, draws, losses,
            gf, ga, gd, pts, rank}]
    """
    league_id, ccode3 = LEAGUES[league_cn]
    data = _request("/leagues", {"id": league_id, "ccode3": ccode3})
    return parse_standings(data)


def parse_fixtures(data):
    """纯解析：联赛 leagues 响应 -> 全部赛程/比分（零网络，可单测）。"""
    fixtures = (data.get("fixtures") or {}).get("allMatches") or []
    out = []
    for m in fixtures:
        st = m.get("status") or {}
        score = (st.get("scoreStr") or "").replace(" ", "").split("-")
        sh = int(score[0]) if len(score) == 2 and score[0].isdigit() else None
        sa = int(score[1]) if len(score) == 2 and score[1].isdigit() else None
        out.append({
            "match_id": str(m.get("id", "")),
            "round": m.get("roundName", m.get("round", "")),
            "utc": st.get("utcTime", ""),
            "finished": bool(st.get("finished")),
            "home": (m.get("home") or {}).get("name", ""),
            "away": (m.get("away") or {}).get("name", ""),
            "home_id": str((m.get("home") or {}).get("id", "")),
            "away_id": str((m.get("away") or {}).get("id", "")),
            "score_home": sh,
            "score_away": sa,
        })
    return out


def get_fixtures(league_cn):
    """
    联赛全部赛程/比分（allMatches，含已赛比分与未赛开球时间）。
    返回 [{match_id, round, utc, finished, home, away,
            home_id, away_id, score_home, score_away}]
    """
    league_id, ccode3 = LEAGUES[league_cn]
    data = _request("/leagues", {"id": league_id, "ccode3": ccode3})
    return parse_fixtures(data)


def parse_matches(data, league_ids=None):
    """纯解析：matches?date 响应 -> 赛程/比分（零网络，可单测）。"""
    if league_ids is None:
        league_ids = {lid for lid, _ in LEAGUES.values()}
    out = []
    for lg in data.get("leagues", []):
        # matches 端点按小组分组: id 是分组 id，primaryId 才是真实联赛 id
        lid = lg.get("primaryId") or lg.get("id")
        if lid not in league_ids and lg.get("id") not in league_ids:
            continue
        for m in lg.get("matches", []):
            st = m.get("status") or {}
            home, away = m.get("home") or {}, m.get("away") or {}
            out.append({
                "match_id": str(m.get("id", "")),
                "utc": st.get("utcTime", ""),
                "finished": bool(st.get("finished")),
                "home": home.get("name", ""),
                "away": away.get("name", ""),
                "home_id": str(home.get("id", "")),
                "away_id": str(away.get("id", "")),
                "score_home": home.get("score"),
                "score_away": away.get("score"),
                "league_id": lid,
                "league_name": lg.get("name", ""),
            })
    return out


def get_matches(date_str=None, league_ids=None):
    """
    按日期取全部联赛赛程/比分。
    date_str: "20261006"；不传为今日。
    league_ids: 只保留这些 FotMob 联赛 id（默认只留本模块低级别联赛）。
    返回 [{match_id, utc, finished, home, away, home_id, away_id,
            score_home, score_away, league_id, league_name}]
    """
    params = {}
    if date_str:
        params["date"] = date_str
    data = _request("/matches", params)
    return parse_matches(data, league_ids)


def parse_team(data, fotmob_id=""):
    """纯解析：teams 响应 -> 球队详情（零网络，可单测）。"""
    det = data.get("details") or {}
    ov = data.get("overview") or {}

    form = []
    for f in ov.get("teamForm") or []:
        tt = f.get("tooltipText") or {}
        form.append({
            "utc": (f.get("date") or {}).get("utcTime", ""),
            "home": tt.get("homeTeam", ""),
            "away": tt.get("awayTeam", ""),
            "score_home": tt.get("homeScore"),
            "score_away": tt.get("awayScore"),
            "result": f.get("resultString", ""),
        })

    fixtures = []
    all_fx = ((data.get("fixtures") or {}).get("allFixtures") or {}).get("fixtures") or []
    for fx in all_fx:
        st = fx.get("status") or {}
        home, away = fx.get("home") or {}, fx.get("away") or {}
        fixtures.append({
            "utc": st.get("utcTime", ""),
            "finished": bool(st.get("finished")),
            "home": home.get("name", ""),
            "away": away.get("name", ""),
            "score_home": home.get("score"),
            "score_away": away.get("score"),
            "result": fx.get("result"),  # 1 胜 / 0 平 / -1 负（以本队视角）
        })

    squad_raw = (data.get("squad") or {}).get("squad")
    squad = None
    if squad_raw:
        squad = [{
            "name": p.get("name", ""),
            "position": (p.get("position") or {}).get("label", ""),
            "shirt": p.get("shirtNumber"),
        } for p in squad_raw]

    return {
        "fotmob_id": str(det.get("id", fotmob_id)),
        "name": det.get("name", ""),
        "shortName": det.get("shortName", ""),
        "country": det.get("country", ""),
        "form": form,
        "fixtures": fixtures,
        "squad": squad,
    }


def get_team(fotmob_id):
    """
    球队详情: 基本信息 + 近况(teamForm) + 全部赛程比分 + 阵容。
    阵容休赛期可能为 null，此时 squad=None。
    返回 {fotmob_id, name, shortName, country,
            form: [{utc, home, away, score_home, score_away, result}],
            fixtures: [{utc, finished, home, away, score_home, score_away,
                        home_win}], squad: [...] | None}
    """
    data = _request("/teams", {"id": fotmob_id})
    return parse_team(data, fotmob_id)


def get_match(match_id):
    """
    单场比分/状态。注意 odds 恒为 null（无赔率），不解析。
    返回 {match_id, utc, finished, home, away, score_home, score_away}
    """
    m = _request("/match", {"id": match_id})
    st = m.get("status") or {}
    home, away = m.get("home") or {}, m.get("away") or {}
    return {
        "match_id": str(m.get("id", match_id)),
        "utc": st.get("utcTime", ""),
        "finished": bool(st.get("finished")),
        "home": home.get("name", ""),
        "away": away.get("name", ""),
        "score_home": m.get("homeScore"),
        "score_away": m.get("awayScore"),
    }


if __name__ == "__main__":
    print("=== FotMob API 测试 ===")
    try:
        ms = get_matches()
        print(f"今日低级别联赛: {len(ms)}场")
        for m in ms[:3]:
            print(f"  {m['league_name']}: {m['home']} vs {m['away']}")
    except Exception as e:
        print(f"matches 失败: {e}")
    for cn in ["J2联赛", "巴西乙"]:
        try:
            st = get_standings(cn)
            fx = get_fixtures(cn)
            fin = sum(1 for x in fx if x["finished"])
            print(f"{cn}: 积分榜{len(st)}队 赛程{len(fx)}场(已赛{fin})")
            if st:
                t0 = st[0]
                print(f"  榜首: {t0['name']} {t0['played']}场 {t0['pts']}分")
        except Exception as e:
            print(f"{cn} 失败: {e}")
