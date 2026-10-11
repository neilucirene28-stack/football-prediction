"""OddsPapi 采集器测试：纯解析 + 配额计数器 + key 缺失（零网络）。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "collector" / "sources"))

import oddspapi  # noqa: E402

SAMPLE_ODDS = {
    "fixtureId": "id1000001766905890",
    "bookmakerOdds": {
        "pinnacle": {
            "markets": {
                "101": {"outcomes": {
                    "101": {"players": {"0": {"price": 3.77}}},
                    "102": {"players": {"0": {"price": 3.90}}},
                    "103": {"players": {"0": {"price": 1.952}}},
                }},
                "1010": {"outcomes": {
                    "1": {"players": {"0": {"price": 1.85}}},
                }},
            }
        },
        "bet365": {
            "markets": {
                "101": {"outcomes": {
                    "101": {"players": {"0": {"price": 3.60}}},
                    "102": {"players": {"0": {"price": 3.80}}},
                    # 缺客胜：残缺市场
                }},
            }
        },
        "empty-book": {"markets": {}},
    },
}


def test_parse_odds_structure():
    p = oddspapi.parse_odds(SAMPLE_ODDS)
    assert p["fixture_id"] == "id1000001766905890"
    assert set(p["bookmakers"]) == {"pinnacle", "bet365"}  # 空市场书被剔除
    pin = p["bookmakers"]["pinnacle"]
    assert pin["101"] == {"101": 3.77, "102": 3.90, "103": 1.952}
    assert pin["1010"] == {"1": 1.85}


def test_get_1x2():
    p = oddspapi.parse_odds(SAMPLE_ODDS)
    assert oddspapi.get_1x2(p, "pinnacle") == (3.77, 3.90, 1.952)
    assert oddspapi.get_1x2(p, "bet365") is None  # 残缺三元组返回 None
    assert oddspapi.get_1x2(p, "nope") is None


def test_parse_fixtures():
    fx = oddspapi.parse_fixtures([
        {"fixtureId": "f1", "participant1Name": "Arsenal",
         "participant2Name": "Chelsea", "tournamentName": "Premier League",
         "startTime": "2026-10-10T14:00:00Z", "hasOdds": True},
        {"fixtureId": "f2", "participant1Name": "A", "hasOdds": False},
    ])
    assert len(fx) == 2
    assert fx[0]["home"] == "Arsenal" and fx[0]["away"] == "Chelsea"
    assert fx[0]["tournament"] == "Premier League" and fx[0]["has_odds"] is True
    assert fx[1]["away"] == "" and fx[1]["has_odds"] is False
    assert oddspapi.parse_fixtures([]) == []


def test_quota_counter_monthly(tmp_path, monkeypatch):
    qf = tmp_path / "q.json"
    monkeypatch.setattr(oddspapi, "QUOTA_FILE", str(qf))
    assert oddspapi.quota_used() == 0
    oddspapi._charge(5)
    assert oddspapi.quota_used() == 5
    oddspapi._charge(2)
    assert oddspapi.quota_used() == 7
    # 计数器持久化
    assert oddspapi._load_quota()[oddspapi._today_month()] == 7
    # 超限抛异常
    monkeypatch.setattr(oddspapi, "MONTHLY_CAP", 7)
    with pytest.raises(RuntimeError, match="超限"):
        oddspapi._check_quota(1)
    oddspapi._check_quota(0)  # 恰好到上限不抛


def test_no_key_raises_helpful_error(monkeypatch):
    # 明确模拟有／无Vault；测试不依赖MUSE本机安装。
    import sys
    from types import SimpleNamespace
    monkeypatch.delenv("ODDSPAPI_KEY", raising=False)
    monkeypatch.setitem(sys.modules, "dynamic_credentials", None)
    with pytest.raises(RuntimeError, match="ODDSPAPI_KEY"):
        oddspapi._get_key()
    monkeypatch.setitem(sys.modules, "dynamic_credentials", SimpleNamespace(
        url_with_surrogate_query_param=lambda *args, **kwargs: None))
    key = oddspapi._get_key()
    assert key == "__VAULT__"  # vault 可用时不抛错
    monkeypatch.setenv("ODDSPAPI_KEY", "  demo-key  ")
    assert oddspapi._get_key() == "demo-key"  # strip，env 优先
