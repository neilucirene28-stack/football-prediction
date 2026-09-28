"""No production database, external requests, or real keys used in tests."""
import json
from datetime import datetime, timedelta, timezone
import pytest
from app.services.deepseek_analysis import AnalysisError, analyze_with_deepseek, prepare_analysis_input, validate_analysis_result

NOW = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)


def sample():
    form = {"played": 5, "wins": 2, "draws": 2, "losses": 1,
            "observed_at": (NOW - timedelta(hours=1)).isoformat(), "source_url": "https://example.org/source"}
    return {"home_team": "甲队", "away_team": "乙队", "kickoff_at": (NOW + timedelta(hours=3)).isoformat(),
            "team_form": {"home": form.copy(), "away": form.copy()},
            "sporttery_odds": [{"market": "wdl", "home_odds": "2.1", "draw_odds": "3.2", "away_odds": "3.4", "observed_at": NOW.isoformat()}]}


def test_valid_explicit_evidence():
    x = prepare_analysis_input(sample(), now=NOW)
    assert set(x) == {"home_team", "away_team", "kickoff_at", "team_form", "sporttery_odds"}


def test_historical_refused():
    x = sample(); x["kickoff_at"] = NOW.isoformat()
    with pytest.raises(AnalysisError, match="historical"):
        prepare_analysis_input(x, now=NOW)


def test_missing_form_refused():
    x = sample(); x.pop("team_form")
    with pytest.raises(AnalysisError, match="team_form"):
        prepare_analysis_input(x, now=NOW)


def test_future_evidence_refused():
    x = sample(); x["team_form"]["home"]["observed_at"] = (NOW + timedelta(minutes=3)).isoformat()
    with pytest.raises(AnalysisError, match="not pre-match"):
        prepare_analysis_input(x, now=NOW)


def test_invalid_odds_refused():
    x = sample(); x["sporttery_odds"][0]["home_odds"] = "NaN"
    with pytest.raises(AnalysisError, match="odds missing"):
        prepare_analysis_input(x, now=NOW)


def test_disabled_never_calls(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-test-key")
    called = []
    with pytest.raises(AnalysisError, match="disabled"):
        analyze_with_deepseek(sample(), now=NOW, transport=lambda *a: called.append(a))
    assert called == []


def test_env_gate_never_calls(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_ANALYSIS_ENABLED", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-test-key")
    called = []
    with pytest.raises(AnalysisError, match="ENABLED"):
        analyze_with_deepseek(sample(), enabled=True, now=NOW, transport=lambda *a: called.append(a))
    assert called == []


def test_explicit_api_call_with_fake_transport(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-test-key")
    calls = []
    content = {"status": "analysis_only", "summary": "样本较少，不能推出结果。", "evidence": ["甲队近五场2胜2平1负"], "limitations": "来源未独立核验。", "predictions": None}
    def fake(url, body, key, timeout):
        calls.append((url, json.loads(body), key, timeout))
        return json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}).encode()
    result = analyze_with_deepseek(sample(), enabled=True, now=NOW, transport=fake)
    assert result["api_called"] is True and result["predictions"] is None
    assert len(calls) == 1 and calls[0][0].startswith("https://api.deepseek.com/")
    assert "dummy-test-key" not in json.dumps(calls[0][1])


def test_model_predictions_rejected():
    with pytest.raises(AnalysisError, match="forbidden"):
        validate_analysis_result({"status": "analysis_only", "summary": "x", "evidence": ["a"], "limitations": "x", "predictions": {"home": 90}})


def test_model_error_no_secret_leak(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_ANALYSIS_ENABLED", "true")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-test-key")
    def fake(*args):
        raise TimeoutError("dummy-test-key")
    with pytest.raises(AnalysisError) as info:
        analyze_with_deepseek(sample(), enabled=True, now=NOW, transport=fake)
    assert "dummy-test-key" not in str(info.value)
