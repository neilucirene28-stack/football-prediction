import json
import pytest
from tools_deepseek_connectivity import ConnectivityError, probe


def test_flag_disabled_blocks_network_and_prompt():
    with pytest.raises(ConnectivityError, match="disabled"):
        probe(enabled=False, environment={"DEEPSEEK_ANALYSIS_ENABLED": "true"},
              transport=lambda *args: pytest.fail("network called"),
              secret_reader=lambda prompt: pytest.fail("key prompt called"))


def test_env_disabled_blocks_network():
    with pytest.raises(ConnectivityError, match="disabled"):
        probe(enabled=True, environment={}, transport=lambda *a: pytest.fail("network called"))


def test_invalid_model_blocks_network():
    with pytest.raises(ConnectivityError, match="model"):
        probe(enabled=True, environment={"DEEPSEEK_ANALYSIS_ENABLED": "true", "DEEPSEEK_MODEL": "../invalid"},
              transport=lambda *a: pytest.fail("network called"))


def test_one_call_with_hidden_key_and_no_key_in_result():
    seen = []
    def fake(url, body, key, timeout):
        seen.append((url, json.loads(body), key, timeout))
        return json.dumps({"choices": [{"message": {"content": "连接成功"}}]}).encode()
    result = probe(enabled=True, environment={"DEEPSEEK_ANALYSIS_ENABLED": "true"},
                   transport=fake, secret_reader=lambda prompt: "fake-key-test")
    assert result["status"] == "connected" and result["request_count"] == 1
    assert "fake-key-test" not in json.dumps(result)
    assert len(seen) == 1 and seen[0][0].startswith("https://api.deepseek.com/")
    assert seen[0][2] == "fake-key-test" and seen[0][3] == 12
    assert "fake-key-test" not in json.dumps(seen[0][1])


def test_transport_exception_does_not_leak_key():
    def fail(*args):
        raise TimeoutError("fake-key-test")
    with pytest.raises(ConnectivityError) as info:
        probe(enabled=True, environment={"DEEPSEEK_ANALYSIS_ENABLED": "true", "DEEPSEEK_API_KEY": "fake-key-test"},
              transport=fail)
    assert "fake-key-test" not in str(info.value)


def test_malformed_or_empty_response_rejected():
    for reply in (b"{}", b"{bad json", b"", b"x" * 8193):
        with pytest.raises(ConnectivityError):
            probe(enabled=True, environment={"DEEPSEEK_ANALYSIS_ENABLED": "true", "DEEPSEEK_API_KEY": "fake-key-test"},
                  transport=lambda *args: reply)


def test_missing_key_rejected_without_network():
    with pytest.raises(ConnectivityError, match="key"):
        probe(enabled=True, environment={"DEEPSEEK_ANALYSIS_ENABLED": "true"},
              secret_reader=lambda _: "", transport=lambda *a: pytest.fail("network called"))
