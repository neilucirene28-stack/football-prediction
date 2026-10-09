"""ESPN summary 端点测试：纯解析器 + 真实响应样例（零网络）。

fixture: tests/fixtures/espn_summary_401841169.json
（2026-10-09 实测，巴西甲 São Paulo 1-2 Santos，完场；
已瘦身：只保留 header.competitions / rosters / keyEvents / boxscore，
球员 athlete 只留 id/displayName/shortName）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector" / "sources"))

import espn  # noqa: E402
from espn import _parse_summary, get_lineups  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def _load():
    with open(FIX / "espn_summary_401841169.json", encoding="utf-8") as f:
        return json.load(f)


def test_parse_match_info():
    s = _parse_summary(_load())
    assert s["event_id"] == "401841169"
    assert s["home"] == "São Paulo"
    assert s["away"] == "Santos"
    assert s["score_home"] == "1" and s["score_away"] == "2"
    assert "FT" in s["status"]


def test_parse_lineups():
    s = _parse_summary(_load())
    lu = s["lineups"]
    for side in ("home", "away"):
        starters = [p for p in lu[side] if p["starter"]]
        subs = [p for p in lu[side] if not p["starter"]]
        assert len(starters) == 11, side
        assert len(subs) >= 5, side
    # 门将应在首发，位置缩写 G
    gk = [p for p in lu["away"] if p["position"] == "G" and p["starter"]]
    assert gk and gk[0]["name"] == "Diogenes"
    # 球员级统计：进 2 球的 Gabriel Barbosa
    gabi = [p for p in lu["away"] if p["name"] == "Gabriel Barbosa"][0]
    assert gabi["stats"].get("totalGoals") == "2"
    assert gabi["starter"] is True


def test_parse_events():
    s = _parse_summary(_load())
    evs = s["events"]
    goals = [e for e in evs if "Goal" in e["type"] or "Penalty" in e["type"]]
    cards = [e for e in evs if "Card" in e["type"]]
    subs = [e for e in evs if "Substitution" in e["type"]]
    assert len(goals) == 3  # 34'点球 + 36' + 54'
    assert len(cards) == 4  # 4 张黄牌
    assert len(subs) >= 5
    goal_minutes = sorted(e["minute"] for e in goals)
    assert goal_minutes == ["34'", "36'", "54'"]
    assert all("minute" in e and "type" in e and "text" in e for e in evs)


def test_parse_team_stats():
    s = _parse_summary(_load())
    st = s["stats"]
    assert st["home"]["totalShots"] == "20"
    assert st["away"]["totalShots"] == "9"
    assert st["home"]["possessionPct"] == "59.5"
    assert st["home"]["wonCorners"] == "14"
    assert st["away"]["wonCorners"] == "1"
    assert st["away"]["yellowCards"] == "3"


def test_parse_prematch_empty():
    # 赛前未公布阵容：rosters 条目存在但 roster 为空，keyEvents/统计为空
    s = _parse_summary({
        "header": {"competitions": [{
            "id": "401879268", "date": "2026-10-10T11:30Z",
            "status": {"type": {"shortDetail": "Scheduled"}},
            "competitors": [
                {"homeAway": "home", "team": {"displayName": "Arsenal"}, "score": "0"},
                {"homeAway": "away", "team": {"displayName": "Leeds United"}, "score": "0"},
            ],
        }]},
        "rosters": [
            {"team": {"displayName": "Arsenal"}, "roster": []},
            {"team": {"displayName": "Leeds United"}, "roster": []},
        ],
        "keyEvents": [],
        "boxscore": {"teams": [
            {"homeAway": "home", "team": {"displayName": "Arsenal"}, "statistics": []},
            {"homeAway": "away", "team": {"displayName": "Leeds United"}, "statistics": []},
        ]},
    })
    assert s["lineups"] == {"home": [], "away": []}
    assert s["events"] == []
    assert s["stats"] == {"home": {}, "away": {}}
    assert s["home"] == "Arsenal"


def test_get_lineups_shortcut(monkeypatch):
    # get_lineups 只走 _request，不应碰网络
    data = _load()
    monkeypatch.setattr(espn, "_request", lambda path, params=None: data)
    lu = get_lineups("401841169", "巴西甲")
    assert set(lu.keys()) == {"home", "away"}
    assert len([p for p in lu["home"] if p["starter"]]) == 11
    assert "events" not in lu
