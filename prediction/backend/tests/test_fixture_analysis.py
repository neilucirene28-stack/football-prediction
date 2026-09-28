"""No production database, keys or network. Only fake transport is used."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tools_fixture_analysis import OperatorError, run_fixture

NOW = datetime(2026, 9, 21, 10, tzinfo=timezone.utc)


def fixture(tmp_path):
    form = dict(played=5, wins=2, draws=2, losses=1,
                observed_at=(NOW - timedelta(hours=1)).isoformat(),
                source_url="https://example.org/test-evidence")
    data = dict(home_team="甲队", away_team="乙队",
                kickoff_at=(NOW + timedelta(hours=2)).isoformat(),
                team_form=dict(home=form, away=form),
                sporttery_odds=[dict(market="wdl", home_odds="2.0",
                                     draw_odds="3.1", away_odds="3.8",
                                     observed_at=(NOW - timedelta(minutes=30)).isoformat())])
    target = tmp_path / "fixture.json"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    return target


def fake_transport(calls):
    def send(url, body, key, timeout):
        calls.append((url, json.loads(body), key, timeout))
        result = dict(status="analysis_only", summary="仅汇总已提供资料。",
                      evidence=["双方近五场记录已提供。"], limitations="来源真实性待人工核实。", predictions=None)
        return json.dumps({"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]}).encode()
    return send


def test_disabled_never_prompts_or_calls(tmp_path):
    calls = []; prompts = []
    with pytest.raises(OperatorError, match="disabled"):
        run_fixture(str(fixture(tmp_path)), enabled=False, environ={},
                    reader=lambda x: prompts.append(x), transport=fake_transport(calls), now=NOW)
    assert calls == prompts == []


def test_invalid_fixture_rejected_before_secret(tmp_path):
    path = fixture(tmp_path)
    data = json.loads(path.read_text()); data["kickoff_at"] = (NOW - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(data))
    prompts = []; calls = []
    with pytest.raises(ValueError, match="historical"):
        run_fixture(str(path), enabled=True, environ={"DEEPSEEK_ANALYSIS_ENABLED": "true"},
                    reader=lambda x: prompts.append(x), transport=fake_transport(calls), now=NOW)
    assert calls == prompts == []


def test_key_hidden_and_only_one_call(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_ANALYSIS_ENABLED", raising=False)
    calls = []; prompts = []
    def reader(prompt):
        prompts.append(prompt)
        return "fake-test-secret"
    result = run_fixture(str(fixture(tmp_path)), enabled=True,
                         environ={"DEEPSEEK_ANALYSIS_ENABLED": "true"}, reader=reader,
                         transport=fake_transport(calls), now=NOW)
    assert len(calls) == len(prompts) == 1
    assert calls[0][2] == "fake-test-secret"
    assert "fake-test-secret" not in json.dumps(result, ensure_ascii=False)
    assert "fake-test-secret" not in json.dumps(calls[0][1], ensure_ascii=False)
    assert "DEEPSEEK_API_KEY" not in __import__("os").environ
    assert result["predictions"] is None


def test_error_restores_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "pre-existing-test")
    monkeypatch.delenv("DEEPSEEK_ANALYSIS_ENABLED", raising=False)
    def fail(*args):
        raise TimeoutError("private credential appears here")
    with pytest.raises(ValueError) as exc:
        run_fixture(str(fixture(tmp_path)), enabled=True,
                    environ={"DEEPSEEK_ANALYSIS_ENABLED": "true", "DEEPSEEK_API_KEY": "pre-existing-test"}, transport=fail, now=NOW)
    assert "private credential" not in str(exc.value)
    assert __import__("os").environ["DEEPSEEK_API_KEY"] == "pre-existing-test"
    assert "DEEPSEEK_ANALYSIS_ENABLED" not in __import__("os").environ


def test_rejects_symlink_and_oversized_file(tmp_path):
    original = fixture(tmp_path)
    link = tmp_path / "alias.json"; link.symlink_to(original)
    with pytest.raises(OperatorError, match="non-symlink"):
        run_fixture(str(link), enabled=True, environ={"DEEPSEEK_ANALYSIS_ENABLED": "true"})
    big = tmp_path / "big.json"; big.write_bytes(b"x" * 32001)
    with pytest.raises(OperatorError, match="too large"):
        run_fixture(str(big), enabled=True, environ={"DEEPSEEK_ANALYSIS_ENABLED": "true"})
