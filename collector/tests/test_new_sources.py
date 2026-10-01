"""8 个新数据源模块的解析/边界测试：全部使用合成载荷，零网络。

约定：
- 每个源的网络层 (`_request` / `get_results` / `download_csv`) 用
  monkeypatch 替换为返回合成载荷的桩，只测解析逻辑。
- 空响应 / 缺字段 → 返回 [] 或 {}，不断言崩溃。
- 错误载荷 (API errors / unknown_season) → 按各模块约定的错误结构处理
  (抛 RuntimeError，或捕获后返回 [] 并打印告警)。
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "collector" / "sources"
sys.path.insert(0, str(SRC_DIR))

import apifootball
import espn
import fd_co_uk
import footballcharts
import footballdata
import oddsapi
import openmodel
import thesportsdb


# ============ API-Football ============

AF_FIXTURES = {
    "errors": [],
    "response": [
        {
            "fixture": {
                "id": 1200001,
                "date": "2026-10-02T19:30:00+00:00",
                "status": {"short": "NS", "elapsed": None},
            },
            "league": {"name": "Premier League"},
            "teams": {
                "home": {"id": 42, "name": "Arsenal"},
                "away": {"id": 49, "name": "Chelsea"},
            },
            "goals": {"home": None, "away": None},
        },
        {
            "fixture": {
                "id": 1200002,
                "date": "2026-10-01T17:00:00+00:00",
                "status": {"short": "FT", "elapsed": 90},
            },
            "league": {"name": "Premier League"},
            "teams": {
                "home": {"id": 50, "name": "Manchester City"},
                "away": {"id": 40, "name": "Liverpool"},
            },
            "goals": {"home": 2, "away": 1},
        },
    ],
}

AF_STANDINGS = {
    "response": [
        {
            "league": {
                "standings": [
                    [
                        {
                            "rank": 1,
                            "team": {"name": "Arsenal"},
                            "all": {
                                "played": 7, "win": 6, "draw": 0, "lose": 1,
                                "goals": {"for": 15, "against": 4},
                            },
                            "points": 18,
                        },
                        {
                            "rank": 2,
                            "team": {"name": "Chelsea"},
                            "all": {
                                "played": 7, "win": 5, "draw": 1, "lose": 1,
                                "goals": {"for": 12, "against": 6},
                            },
                            "points": 16,
                        },
                    ]
                ]
            }
        }
    ]
}


def test_apifootball_get_fixtures_parse(monkeypatch):
    monkeypatch.setattr(apifootball, "_request", lambda *a, **k: AF_FIXTURES)
    out = apifootball.get_fixtures("2026-10-02")
    assert len(out) == 2
    m0 = out[0]
    assert m0["fixture_id"] == 1200001
    assert m0["home"] == "Arsenal" and m0["away"] == "Chelsea"
    assert m0["status"] == "NS"
    assert m0["goals_home"] is None  # 未开赛比分填 None
    assert m0["home_id"] == 42
    m1 = out[1]
    assert (m1["goals_home"], m1["goals_away"]) == (2, 1)
    assert m1["status"] == "FT"


def test_apifootball_empty_response(monkeypatch):
    monkeypatch.setattr(apifootball, "_request", lambda *a, **k: {})
    assert apifootball.get_fixtures("2026-10-02") == []
    assert apifootball.get_standings(39, 2026) == []


def test_apifootball_missing_nested_fields(monkeypatch):
    # teams/goals 整段缺失 → 队名填 ""，比分填 None，不崩
    monkeypatch.setattr(
        apifootball, "_request",
        lambda *a, **k: {"response": [{"fixture": {"id": 9, "date": "2026-10-02", "status": {}}}]},
    )
    out = apifootball.get_fixtures("2026-10-02")
    assert len(out) == 1
    assert out[0]["home"] == "" and out[0]["away"] == ""
    assert out[0]["goals_home"] is None


def test_apifootball_api_error_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("API 错误: {'rateLimit': 'Too many requests'}")
    monkeypatch.setattr(apifootball, "_request", boom)
    with pytest.raises(RuntimeError):
        apifootball.get_fixtures("2026-10-02")


def test_apifootball_standings_parse(monkeypatch):
    monkeypatch.setattr(apifootball, "_request", lambda *a, **k: AF_STANDINGS)
    out = apifootball.get_standings(39, 2026)
    assert len(out) == 2
    assert out[0]["rank"] == 1 and out[0]["team"] == "Arsenal"
    assert out[0]["points"] == 18 and out[0]["gf"] == 15 and out[0]["ga"] == 4


# ============ football-data.org ============

FD_MATCHES = {
    "matches": [
        {
            "id": 530001,
            "utcDate": "2026-10-02T19:00:00Z",
            "status": "TIMED",
            "homeTeam": {"name": "Real Madrid CF"},
            "awayTeam": {"name": "FC Barcelona"},
            "score": {"fullTime": {"home": None, "away": None}},
            "competition": {"name": "Primera Division", "code": "PD"},
        },
        {
            "id": 530002,
            "utcDate": "2026-10-01T17:30:00Z",
            "status": "FINISHED",
            "homeTeam": {"name": "Atlético de Madrid"},
            "awayTeam": {"name": "Sevilla FC"},
            "score": {"fullTime": {"home": 1, "away": 1}},
            "competition": {"name": "Primera Division", "code": "PD"},
        },
    ]
}

FD_STANDINGS = {
    "standings": [
        {
            "type": "TOTAL",
            "table": [
                {
                    "position": 1, "team": {"name": "Real Madrid CF"},
                    "playedGames": 8, "won": 7, "draw": 1, "lost": 0,
                    "goalsFor": 20, "goalsAgainst": 6, "points": 22,
                }
            ],
        },
        {"type": "AWAY", "table": [{"position": 1, "team": {"name": "X"}}]},
    ]
}


def test_footballdata_get_matches_parse(monkeypatch):
    monkeypatch.setattr(footballdata, "_request", lambda *a, **k: FD_MATCHES)
    out = footballdata.get_matches("2026-10-01", "2026-10-02")
    assert len(out) == 2
    assert out[0]["home"] == "Real Madrid CF"
    assert out[0]["competition_code"] == "PD"
    assert out[0]["score_home"] is None  # 未开赛
    assert (out[1]["score_home"], out[1]["score_away"]) == (1, 1)


def test_footballdata_null_fulltime_safe(monkeypatch):
    # score.fullTime 为 null（API 偶发）→ 不崩，比分填 None
    payload = {"matches": [{
        "id": 1, "utcDate": "2026-10-02T19:00:00Z", "status": "TIMED",
        "homeTeam": {"name": "A"}, "awayTeam": {"name": "B"},
        "score": {"fullTime": None},
        "competition": {"name": "X", "code": "PL"},
    }]}
    monkeypatch.setattr(footballdata, "_request", lambda *a, **k: payload)
    out = footballdata.get_matches("2026-10-02", "2026-10-02")
    assert out[0]["score_home"] is None and out[0]["score_away"] is None


def test_footballdata_empty(monkeypatch):
    monkeypatch.setattr(footballdata, "_request", lambda *a, **k: {})
    assert footballdata.get_matches("2026-10-02", "2026-10-02") == []


def test_footballdata_standings_only_total(monkeypatch):
    monkeypatch.setattr(footballdata, "_request", lambda *a, **k: FD_STANDINGS)
    out = footballdata.get_standings("PD")
    assert len(out) == 1  # AWAY 表被忽略
    assert out[0]["team"] == "Real Madrid CF" and out[0]["points"] == 22


# ============ The Odds API ============

ODDS_PAYLOAD = [
    {
        "id": "abc123",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": "2026-10-03T16:30:00Z",
        "bookmakers": [
            {
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Arsenal", "price": 2.10},
                            {"name": "Draw", "price": 3.60},
                            {"name": "Chelsea", "price": 3.40},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Arsenal", "price": 1.95, "point": -0.5},
                            {"name": "Chelsea", "price": 1.95, "point": 0.5},
                        ],
                    },
                ],
            },
            {
                "title": "Bet365",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Arsenal", "price": 2.05},
                            {"name": "Draw", "price": 3.50},
                            {"name": "Chelsea", "price": 3.60},
                        ],
                    }
                ],
            },
        ],
    }
]


def test_oddsapi_get_odds_parse(monkeypatch):
    monkeypatch.setattr(oddsapi, "_request", lambda *a, **k: ODDS_PAYLOAD)
    out = oddsapi.get_odds("soccer_epl", markets="h2h", regions="eu")
    assert len(out) == 1
    m = out[0]
    assert m["home"] == "Arsenal" and m["away"] == "Chelsea"
    assert m["commence_time"] == "2026-10-03T16:30:00Z"
    pin = m["bookmakers"]["Pinnacle"]
    assert pin["h2h"] == {"Arsenal": 2.10, "Draw": 3.60, "Chelsea": 3.40}
    assert "spreads" in pin
    assert m["bookmakers"]["Bet365"]["h2h"]["Draw"] == 3.50


def test_oddsapi_no_bookmakers(monkeypatch):
    payload = [{**ODDS_PAYLOAD[0], "bookmakers": []}]
    monkeypatch.setattr(oddsapi, "_request", lambda *a, **k: payload)
    out = oddsapi.get_odds("soccer_epl")
    assert out[0]["bookmakers"] == {}


def test_oddsapi_empty_list(monkeypatch):
    monkeypatch.setattr(oddsapi, "_request", lambda *a, **k: [])
    assert oddsapi.get_odds("soccer_epl") == []


def test_oddsapi_missing_price_ok(monkeypatch):
    # 某 outcome 缺 price → 填 None，不崩
    payload = json.loads(json.dumps(ODDS_PAYLOAD))
    del payload[0]["bookmakers"][0]["markets"][0]["outcomes"][0]["price"]
    monkeypatch.setattr(oddsapi, "_request", lambda *a, **k: payload)
    out = oddsapi.get_odds("soccer_epl")
    assert out[0]["bookmakers"]["Pinnacle"]["h2h"]["Arsenal"] is None


# ============ ESPN ============

ESPN_SB = {
    "leagues": [{"name": "English Premier League"}],
    "events": [
        {
            "id": "700001",
            "date": "2026-10-03T16:30:00Z",
            "competitions": [
                {
                    "status": {"type": {"shortDetail": "FT"}},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "score": "2",
                            "team": {"id": "359", "displayName": "Arsenal"},
                        },
                        {
                            "homeAway": "away",
                            "score": "1",
                            "team": {"id": "363", "displayName": "Chelsea"},
                        },
                    ],
                }
            ],
        },
        {
            "id": "700002",
            "date": "2026-10-04T13:00:00Z",
            "competitions": [
                {
                    "status": {"type": {"shortDetail": "10/4 - 1:00 PM EDT"}},
                    "competitors": [
                        {
                            "homeAway": "home", "score": "",
                            "team": {"id": "364", "displayName": "Liverpool"},
                        },
                        {
                            "homeAway": "away", "score": "",
                            "team": {"id": "361", "displayName": "Manchester United"},
                        },
                    ],
                }
            ],
        },
    ],
}

ESPN_STANDINGS = {
    "children": [
        {
            "standings": {
                "entries": [
                    {
                        "team": {"id": "359", "displayName": "Arsenal"},
                        "stats": [
                            {"name": "gamesPlayed", "value": 7},
                            {"name": "wins", "value": 6},
                            {"name": "ties", "value": 0},
                            {"name": "losses", "value": 1},
                            {"name": "pointsFor", "value": 15},
                            {"name": "pointsAgainst", "value": 4},
                            {"name": "points", "value": 18},
                        ],
                    }
                ]
            }
        }
    ]
}


def test_espn_scoreboard_parse(monkeypatch):
    monkeypatch.setattr(espn, "_request", lambda *a, **k: ESPN_SB)
    out = espn.get_scoreboard("英超")
    assert len(out) == 2
    m0 = out[0]
    assert m0["event_id"] == "700001"
    assert m0["home"] == "Arsenal" and m0["away"] == "Chelsea"
    assert m0["score_home"] == "2" and m0["score_away"] == "1"
    assert m0["status"] == "FT"
    assert m0["home_id"] == "359"
    assert m0["league"] == "English Premier League"


def test_espn_scoreboard_no_events(monkeypatch):
    monkeypatch.setattr(espn, "_request", lambda *a, **k: {})
    assert espn.get_scoreboard("英超") == []


def test_espn_scoreboard_missing_competitors(monkeypatch):
    payload = {"events": [{"id": "1", "date": "2026-10-03T16:30:00Z", "competitions": []}],
               "leagues": [{"name": "X"}]}
    monkeypatch.setattr(espn, "_request", lambda *a, **k: payload)
    out = espn.get_scoreboard("英超")
    assert len(out) == 1
    assert out[0]["home"] == "" and out[0]["away"] == ""


def test_espn_standings_parse(monkeypatch):
    monkeypatch.setattr(espn, "_request", lambda *a, **k: ESPN_STANDINGS)
    out = espn.get_standings("英超")
    assert len(out) == 1
    t = out[0]
    assert t["team"] == "Arsenal"
    assert t["played"] == 7 and t["won"] == 6 and t["points"] == 18
    assert t["gf"] == 15 and t["ga"] == 4


# ============ football-data.co.uk ============

FDCOUK_ROWS = [
    {
        "Date": "03/10/2026", "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "FTHG": "2", "FTAG": "1", "HTHG": "1", "HTAG": "0", "FTR": "H",
        "HS": "14", "AS": "9", "HC": "7", "AC": "4",
        "B365H": "2.10", "B365D": "3.60", "B365A": "3.40",
        "PSCH": "2.15", "PSCD": "3.55", "PSCA": "3.45",
    },
    {
        "Date": "03/10/2026", "HomeTeam": "Liverpool", "AwayTeam": "Everton",
        "FTHG": "0", "FTAG": "0", "HTHG": "0", "HTAG": "0", "FTR": "D",
        "HS": "18", "AS": "6", "HC": "9", "AC": "2",
        "B365H": "1.50", "B365D": "4.20", "B365A": "6.50",
        "PSCH": "1.53", "PSCD": "4.10", "PSCA": "6.20",
    },
]


def _write_csv(tmp_path, rows):
    p = tmp_path / "E0_2627.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return str(p)


def test_fd_co_uk_parse_csv(tmp_path):
    path = _write_csv(tmp_path, FDCOUK_ROWS)
    out = fd_co_uk.parse_csv(path)
    assert len(out) == 2
    m = out[0]
    assert m["home"] == "Arsenal" and m["away"] == "Chelsea"
    assert (m["score_home"], m["score_away"]) == (2, 1)
    assert (m["score_ht_home"], m["score_ht_away"]) == (1, 0)
    assert m["result"] == "H"
    assert m["shots_home"] == 14 and m["corners_away"] == 4
    assert m["odds_home"] == 2.10 and m["odds_close_away"] == 3.45
    # 2026-10-01 实测：120 列表头无 HxG/AxG，解析结果不应含 xG 字段
    assert "xg_home" not in m and "HxG" not in m


def test_fd_co_uk_bad_numbers(tmp_path):
    rows = [dict(FDCOUK_ROWS[0], FTHG="?", B365H="", HS="N/A")]
    path = _write_csv(tmp_path, rows)
    out = fd_co_uk.parse_csv(path)
    assert out[0]["score_home"] is None
    assert out[0]["odds_home"] is None
    assert out[0]["shots_home"] is None


def test_fd_co_uk_empty_csv(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("Date,HomeTeam,AwayTeam,FTHG,FTAG\n", encoding="utf-8")
    assert fd_co_uk.parse_csv(str(p)) == []


def test_fd_co_uk_helpers():
    assert fd_co_uk._int("3") == 3
    assert fd_co_uk._int("") is None
    assert fd_co_uk._int("x") is None
    assert fd_co_uk._float("2.5") == 2.5
    assert fd_co_uk._float(None) is None


# ============ thesportsdb ============

TSDB_TEAMS = {
    "teams": [
        {
            "strTeam": "Arsenal",
            "idTeam": "133604",
            "idESPN": "359",
            "idAPIfootball": "42",
            "strCountry": "England",
        },
        {
            "strTeam": "Chelsea",
            "idTeam": "133610",
            "idESPN": "363",
            "idAPIfootball": "49",
            "strCountry": "England",
        },
    ]
}

TSDB_EVENTS = {
    "events": [
        {
            "idEvent": "2000001",
            "dateEvent": "2026-10-03",
            "strHomeTeam": "Arsenal",
            "strAwayTeam": "Chelsea",
            "strLeague": "English Premier League",
        }
    ]
}


def test_thesportsdb_search_teams_parse(monkeypatch):
    monkeypatch.setattr(thesportsdb, "_request", lambda *a, **k: TSDB_TEAMS)
    out = thesportsdb.search_teams("English Premier League")
    assert len(out) == 2
    assert out[0]["name"] == "Arsenal"
    assert out[0]["espn_id"] == "359"
    assert out[0]["apifootball_id"] == "42"
    assert out[0]["thesportsdb_id"] == "133604"


def test_thesportsdb_search_teams_none(monkeypatch):
    monkeypatch.setattr(thesportsdb, "_request", lambda *a, **k: {"teams": None})
    assert thesportsdb.search_teams("No Such League") == []


def test_thesportsdb_get_next_events(monkeypatch):
    monkeypatch.setattr(thesportsdb, "_request", lambda *a, **k: TSDB_EVENTS)
    out = thesportsdb.get_next_events("133604")
    assert len(out) == 1
    assert out[0]["home"] == "Arsenal" and out[0]["date"] == "2026-10-03"


def test_thesportsdb_lookup(monkeypatch, tmp_path):
    cache = tmp_path / "team_id_map.json"
    cache.write_text(json.dumps({"arsenal": {"name": "Arsenal", "espn_id": "359"}}),
                     encoding="utf-8")
    monkeypatch.setattr(thesportsdb, "CACHE_FILE", str(cache))
    assert thesportsdb.lookup("Arsenal")["espn_id"] == "359"
    assert thesportsdb.lookup("arsenal")["espn_id"] == "359"  # 大小写不敏感
    assert thesportsdb.lookup("NoSuchTeam") is None


# ============ football-charts ============

FC_MATCHES = [
    {"date": "2026-03-08", "homeTeam": "磐田喜悦", "awayTeam": "甲府风林",
     "score": "2:1", "ht_result": "1:0"},
    {"date": "2026-03-07", "homeTeam": "清水心跳", "awayTeam": "横滨FC",
     "score": "0:0", "ht_result": "0:0"},
    {"date": "2026-03-09", "homeTeam": "长崎航海", "awayTeam": "大分三神",
     "score": "延期", "ht_result": ""},  # 非法比分 → 跳过
    {"date": "2026-03-10", "homeTeam": "仙台七夕", "awayTeam": "山形山神",
     "score": "", "ht_result": ""},  # 空比分 → 跳过
]


def test_footballcharts_results_to_history():
    out = footballcharts.results_to_history(FC_MATCHES)
    assert len(out) == 2  # 非法/空比分被跳过
    # 按日期排序
    assert out[0]["date"] == "2026-03-07"
    assert out[0]["home"] == "清水心跳"
    assert (out[0]["hg"], out[0]["ag"]) == (0, 0)
    assert (out[0]["hthg"], out[0]["htag"]) == (0, 0)
    m = out[1]
    assert (m["hg"], m["ag"]) == (2, 1)
    assert (m["hthg"], m["htag"]) == (1, 0)
    assert m["source"] == "football-charts"


def test_footballcharts_results_to_history_empty():
    assert footballcharts.results_to_history([]) == []


def test_footballcharts_unknown_season_no_crash(monkeypatch):
    # J2 2026 赛季不存在 → get_results 抛错，fetch 捕获后返回 []
    def boom(league_code, season=None):
        raise RuntimeError("football-charts API 404: unknown_season")
    monkeypatch.setattr(footballcharts, "get_results", boom)
    out = footballcharts.fetch_league_history("japan2", ["2026"])
    assert out == []


def test_footballcharts_parse_held_seasons():
    msg = '"japan2" has no season "2026". Seasons held: 2025, 2024, 2023'
    assert footballcharts.parse_held_seasons(msg) == ["2025", "2024", "2023"]
    assert footballcharts.parse_held_seasons("some other error") == []
    assert footballcharts.parse_held_seasons("") == []


def test_footballcharts_is_unknown_season_error():
    assert footballcharts.is_unknown_season_error(
        RuntimeError("HTTP 400: unknown_season")) is True
    assert footballcharts.is_unknown_season_error(
        RuntimeError("请求超时")) is False


def test_footballcharts_fetch_dedup(monkeypatch):
    dupes = [
        {"date": "2025-01-01", "homeTeam": "A", "awayTeam": "B",
         "score": "1:0", "ht_result": "0:0"},
    ]
    monkeypatch.setattr(footballcharts, "get_results", lambda *a, **k: dupes)
    out = footballcharts.fetch_league_history("japan2", ["2024", "2025"])
    assert len(out) == 1  # 跨赛季去重


# ============ theopenmodel ============

OM_CSV_HEADER = ("kickoff,league,home,away,pHome,pDraw,pAway,modelPick,"
                 "result,correct\n")
OM_FUTURE = "2026-12-01T15:00:00Z,premier-league,Arsenal,Chelsea,0.55,0.25,0.20,home,,\n"
OM_FINISHED = "2026-09-11T15:00:00Z,premier-league,Liverpool,Everton,0.60,0.22,0.18,home,2-0,true\n"
OM_PAST_NO_RESULT = "2020-01-01T15:00:00Z,la-liga,Real Madrid,Barcelona,0.45,0.27,0.28,home,,\n"
OM_BAD_ROW = "not-a-date,serie-a,Inter,Milan,0.5,0.25,0.25,home,,\n"


def _om_csv(tmp_path, rows):
    p = tmp_path / "predictions.csv"
    p.write_text(OM_CSV_HEADER + "".join(rows), encoding="utf-8")
    return p


@pytest.fixture()
def om_cache(monkeypatch, tmp_path):
    """把 openmodel 缓存目录指到 tmp，保证零网络（缓存新鲜不下载）。"""
    monkeypatch.setattr(openmodel, "CACHE_DIR", str(tmp_path))
    return tmp_path


def test_openmodel_parses_upcoming(om_cache):
    _om_csv(om_cache, [OM_FUTURE, OM_FINISHED])
    out = openmodel.get_predictions()
    assert len(out) == 1  # 已完赛的被过滤
    p = out[0]
    assert p["home"] == "Arsenal" and p["away"] == "Chelsea"
    assert p["league"] == "英超"  # 联赛代码映射中文
    assert p["league_code"] == "premier-league"
    assert abs(p["p_home"] - 0.55) < 1e-9
    assert p["model_pick"] == "home"
    assert p["kickoff"].tzinfo is not None


def test_openmodel_filters_past_kickoff(om_cache):
    # kickoff 已过但 result 为空的陈旧行 → 必须过滤（防泄漏）
    _om_csv(om_cache, [OM_FUTURE, OM_PAST_NO_RESULT])
    out = openmodel.get_predictions()
    assert len(out) == 1
    assert out[0]["home"] == "Arsenal"


def test_openmodel_bad_rows_skipped(om_cache):
    _om_csv(om_cache, [OM_FUTURE, OM_BAD_ROW])
    out = openmodel.get_predictions()
    assert len(out) == 1


def test_openmodel_find_match(om_cache):
    _om_csv(om_cache, [OM_FUTURE])
    preds = openmodel.get_predictions()
    hit = openmodel.find_match(preds, "arsenal", "CHELSEA")
    assert hit is not None and hit["home"] == "Arsenal"
    assert openmodel.find_match(preds, "Arsenal", "Tottenham") is None


def test_filter_upcoming_pure():
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    preds = [
        {"kickoff": datetime(2026, 10, 5, tzinfo=timezone.utc)},
        {"kickoff": datetime(2026, 9, 28, tzinfo=timezone.utc)},
        {"kickoff": None},
    ]
    out = openmodel.filter_upcoming(preds, asof=now)
    assert len(out) == 1
    assert out[0]["kickoff"].day == 5


def test_snapshot_is_stale():
    asof = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    assert openmodel.snapshot_is_stale([], asof=asof) is True
    fresh = [{"kickoff": asof - timedelta(days=1)}]
    assert openmodel.snapshot_is_stale(fresh, asof=asof) is False
    old = [{"kickoff": asof - timedelta(days=5)}]
    assert openmodel.snapshot_is_stale(old, asof=asof) is True
    # 边界：正好 3 天不算过期
    edge = [{"kickoff": asof - timedelta(days=3)}]
    assert openmodel.snapshot_is_stale(edge, asof=asof) is False


# ============ _http.py（共享 curl 重试层） ============

try:
    import _http
    _HAS_HTTP = True
except ImportError:
    _HAS_HTTP = False

requires_http = pytest.mark.skipif(not _HAS_HTTP, reason="_http.py 不存在")


class _FakeCompleted:
    def __init__(self, stdout=b"", returncode=0, stderr=b""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _no_sleep(monkeypatch):
    monkeypatch.setattr(_http.time, "sleep", lambda s: None)


def _fake_run_factory(monkeypatch, script):
    """script: list，元素为 _FakeCompleted 或 Exception 实例，按调用顺序返回/抛出。"""
    calls = []
    it = iter(script)

    def fake_run(*a, **k):
        calls.append(a)
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(_http.subprocess, "run", fake_run)
    return calls


@requires_http
def test_http_fetch_retry_then_success(monkeypatch):
    _no_sleep(monkeypatch)
    import subprocess as _sp
    ok = _FakeCompleted(stdout=b'{"ok": true}\n200')
    calls = _fake_run_factory(
        monkeypatch,
        [_sp.TimeoutExpired(cmd="curl", timeout=20),
         _sp.TimeoutExpired(cmd="curl", timeout=20),
         ok],
    )
    out = _http.fetch_with_retry("https://example.com/x")
    assert out == {"ok": True}
    assert len(calls) == 3


@requires_http
def test_http_fetch_exhausted_raises(monkeypatch):
    _no_sleep(monkeypatch)
    import subprocess as _sp
    calls = _fake_run_factory(
        monkeypatch,
        [_sp.TimeoutExpired(cmd="curl", timeout=20)] * 3,
    )
    with pytest.raises(RuntimeError, match="3次重试"):
        _http.fetch_with_retry("https://example.com/x")
    assert len(calls) == 3


@requires_http
def test_http_429_retries(monkeypatch):
    _no_sleep(monkeypatch)
    calls = _fake_run_factory(
        monkeypatch,
        [_FakeCompleted(stdout=b"rate limited\n429"),
         _FakeCompleted(stdout=b'{"ok": true}\n200')],
    )
    out = _http.fetch_with_retry("https://example.com/x")
    assert out == {"ok": True}
    assert len(calls) == 2


@requires_http
def test_http_4xx_no_retry(monkeypatch):
    # 404（如 football-charts unknown_season）→ 直接抛 HTTPError，不重试
    _no_sleep(monkeypatch)
    calls = _fake_run_factory(
        monkeypatch,
        [_FakeCompleted(stdout=b"unknown_season\n404")],
    )
    with pytest.raises(_http.HTTPError) as ei:
        _http.fetch_with_retry("https://example.com/x")
    assert ei.value.code == 404
    assert len(calls) == 1


@requires_http
def test_http_fetch_raw_bytes(monkeypatch):
    _no_sleep(monkeypatch)
    calls = _fake_run_factory(
        monkeypatch,
        [_FakeCompleted(stdout=b"\x89PNG\r\n200")],
    )
    out = _http.fetch_with_retry("https://example.com/x", parse_json=False)
    assert out == b"\x89PNG\r"
    assert len(calls) == 1


@requires_http
def test_http_clean_no_proxy(monkeypatch):
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,[::1]")
    monkeypatch.setenv("NO_PROXY", "")
    cleaned = _http.clean_no_proxy()
    assert "[::1]" not in cleaned
    assert "localhost" in cleaned and "127.0.0.1" in cleaned
    assert "[::1]" not in os.environ.get("no_proxy", "")


@requires_http
def test_http_download_success(monkeypatch, tmp_path):
    _no_sleep(monkeypatch)
    dest = str(tmp_path / "f.csv")
    Path(dest).write_bytes(b"a,b\n1,2\n" * 20)  # 预置足够大的文件
    _fake_run_factory(monkeypatch, [_FakeCompleted(returncode=0)])
    out = _http.download_with_retry("https://example.com/f.csv", dest, min_size=100)
    assert out == dest


@requires_http
def test_http_download_exhausted(monkeypatch, tmp_path):
    _no_sleep(monkeypatch)
    dest = str(tmp_path / "f.csv")
    _fake_run_factory(monkeypatch, [_FakeCompleted(returncode=7)] * 3)
    with pytest.raises(RuntimeError, match="下载失败"):
        _http.download_with_retry("https://example.com/f.csv", dest)

