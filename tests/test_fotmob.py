"""FotMob 采集器测试：纯解析器 + 真实响应样例（零网络）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector" / "sources"))

from fotmob import (  # noqa: E402
    LEAGUES, FOTMOB_LOW_LEAGUES,
    _parse_scores_str,
    parse_standings, parse_fixtures, parse_matches, parse_team,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name):
    with open(FIX / name, encoding="utf-8") as f:
        return json.load(f)


def test_league_ids():
    # 已实测的 9 个低级别联赛 ID（2026-10-06 验证）
    assert LEAGUES["J2联赛"] == (8974, "JPN")
    assert LEAGUES["J3联赛"] == (9136, "JPN")
    assert LEAGUES["K2联赛"] == (9116, "KOR")
    assert LEAGUES["K3联赛"] == (9537, "KOR")
    assert LEAGUES["挪甲"] == (203, "NOR")
    assert LEAGUES["挪乙"] == (204, "NOR")
    assert LEAGUES["瑞典甲"] == (168, "SWE")
    assert LEAGUES["瑞典乙"] == (169, "SWE")
    assert LEAGUES["巴西乙"] == (8814, "BRA")
    assert set(FOTMOB_LOW_LEAGUES) == set(LEAGUES)


def test_parse_scores_str():
    assert _parse_scores_str("17-8") == (17, 8)
    assert _parse_scores_str("0-0") == (0, 0)
    assert _parse_scores_str("") == (None, None)
    assert _parse_scores_str("xx") == (None, None)


def test_parse_standings():
    st = parse_standings(_load("fm_j2.json"))
    assert len(st) == 20
    t0 = st[0]
    assert t0["name"] == "Vegalta Sendai"
    assert t0["fotmob_id"] == "162192"
    assert t0["played"] == 9 and t0["pts"] == 20
    assert t0["gf"] == 17 and t0["ga"] == 8 and t0["gd"] == 9
    assert t0["rank"] == 1
    # 积分 = 3*胜 + 平
    for t in st:
        assert t["pts"] == 3 * t["wins"] + t["draws"]
        assert t["played"] == t["wins"] + t["draws"] + t["losses"]


def test_parse_fixtures():
    fx = parse_fixtures(_load("fm_j2.json"))
    assert len(fx) == 380  # J2 单赛季 20 队双循环
    fin = [m for m in fx if m["finished"]]
    assert len(fin) == 90
    m0 = fin[0]
    assert m0["home"] == "Hokkaido Consadole Sapporo"
    assert m0["score_home"] == 2 and m0["score_away"] == 0
    assert m0["utc"].startswith("2026-")
    unfin = [m for m in fx if not m["finished"]]
    assert unfin and unfin[0]["score_home"] is None


def test_parse_matches_filters_low_leagues():
    ms = parse_matches(_load("fm_matches.json"))
    lids = {m["league_id"] for m in ms}
    assert lids <= {lid for lid, _ in LEAGUES.values()}
    for m in ms:
        assert m["home"] and m["away"] and m["match_id"]


def test_parse_team():
    tm = parse_team(_load("fm_team.json"), "162192")
    assert tm["name"] == "Vegalta Sendai"
    assert tm["fotmob_id"] == "162192"
    assert tm["country"] == "JPN"
    assert len(tm["form"]) > 0
    f0 = tm["form"][0]
    assert f0["result"] in ("W", "D", "L")
    assert f0["score_home"] is not None
    # 休赛期阵容可为 null
    assert tm["squad"] is None or isinstance(tm["squad"], list)
    assert len(tm["fixtures"]) > 0


def test_team_names_normalize_fotmob():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                           / "collector" / "sources"))
    from team_names import normalize
    assert normalize("Vegalta Sendai") == "vegalta sendai"
    assert normalize("São Bernardo FC") == "são bernardo"
