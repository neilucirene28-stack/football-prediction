"""soccerdata 采集器测试: 纯逻辑 + mock 网络, 零网络。"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector" / "sources"))

import soccerdata as sd_mod  # noqa: E402  本目录的 collector/sources/soccerdata.py


def _fake_match_df():
    """两场假比赛的 Understat read_team_match_stats() 格式数据。"""
    return pd.DataFrame([
        {
            "home_team": "Arsenal", "away_team": "Chelsea",
            "home_xg": 2.0, "away_xg": 1.0,
            "home_goals": 2, "away_goals": 1,
            "home_points": 3, "away_points": 0,
            "home_ppda": 8.5, "away_ppda": 12.0,
            "home_deep_completions": 10, "away_deep_completions": 4,
        },
        {
            "home_team": "Chelsea", "away_team": "Arsenal",
            "home_xg": 0.5, "away_xg": 1.5,
            "home_goals": 0, "away_goals": 1,
            "home_points": 0, "away_points": 3,
            "home_ppda": float("nan"), "away_ppda": 9.0,
            "home_deep_completions": 3, "away_deep_completions": 7,
        },
    ])


def test_normalize_season():
    assert sd_mod._normalize_season("2025") == "2025"
    assert sd_mod._normalize_season(2025) == "2025"
    assert sd_mod._normalize_season("2025/26") == "2025"
    assert sd_mod._normalize_season("2526") == "2025"
    assert sd_mod._normalize_season("25/26") == "2025"
    with pytest.raises(ValueError):
        sd_mod._normalize_season("abc")


def test_canonical_league():
    assert sd_mod._canonical_league("英超") == "ENG-Premier League"
    assert sd_mod._canonical_league("ENG-Premier League") == "ENG-Premier League"
    with pytest.raises(ValueError):
        sd_mod._canonical_league("中超")


def test_aggregate_team_xg():
    out = sd_mod._aggregate_team_xg(_fake_match_df())
    by = {t["team"]: t for t in out}
    ars = by["Arsenal"]
    assert ars["played"] == 2
    assert ars["xg"] == pytest.approx(3.5)       # 2.0 + 1.5
    assert ars["xga"] == pytest.approx(1.5)      # 1.0 + 0.5
    assert ars["goals"] == pytest.approx(3.0)
    assert ars["goals_against"] == pytest.approx(1.0)
    assert ars["points"] == pytest.approx(6.0)
    assert ars["xg_per_match"] == pytest.approx(1.75)
    che = by["Chelsea"]
    assert che["played"] == 2
    assert che["xg"] == pytest.approx(1.5)
    assert che["ppda"] == pytest.approx(12.0)    # NaN 的那场不计入平均
    assert ars["deep_completions"] == pytest.approx(8.5)


def test_get_understat_xg_mock(monkeypatch):
    seen = {}

    class FakeUnderstat:
        def __init__(self, leagues=None, seasons=None, proxy=None, no_cache=False):
            seen.update(leagues=leagues, seasons=seasons,
                        proxy=proxy, no_cache=no_cache)

        def read_team_match_stats(self):
            return _fake_match_df()

    monkeypatch.setattr(sd_mod, "Understat", FakeUnderstat)
    out = sd_mod.get_understat_xg("英超", "2025/26")
    assert seen["leagues"] == "ENG-Premier League"
    assert seen["seasons"] == "2025"
    assert len(out) == 2
    assert {t["team"] for t in out} == {"Arsenal", "Chelsea"}


def test_get_club_elo_mock(monkeypatch):
    df = pd.DataFrame(
        {"elo": [1950.0, 2000.0], "rank": [2.0, 1.0],
         "league": ["ENG-Premier League", "ENG-Premier League"]},
        index=pd.Index(["Chelsea", "Arsenal"], name="team"),
    )

    class FakeClubElo:
        def __init__(self, proxy=None, no_cache=False):
            pass

        def read_by_date(self):
            return df

    monkeypatch.setattr(sd_mod, "ClubElo", FakeClubElo)
    out = sd_mod.get_club_elo("英超")
    assert [t["team"] for t in out] == ["Arsenal", "Chelsea"]  # 按 rank 升序
    assert out[0]["elo"] == pytest.approx(2000.0)
    assert out[0]["rank"] == 1
    with pytest.raises(ValueError):
        sd_mod.get_club_elo("中超")  # 联赛名非法
