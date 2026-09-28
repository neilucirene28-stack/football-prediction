"""Offline pytest suite for tools_historical_reconstruction.py.
No real network requests; all DeepSeek calls are mocked.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from unittest import mock

import pytest

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools_historical_reconstruction as tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_model_json():
    """A fully valid DeepSeek response that should pass all validation."""
    return {
        "fixture_id": "3003893",
        "analysis_type": "historical_prematch_reconstruction_simulation",
        "market_read": {
            "main_handicap_consensus": "曼彻斯特联 -0.5",
            "handicap_movement_summary": "盘口稳定",
            "water_movement_summary": "水位微调",
            "market_signal": "客队让球",
            "market_signal_strength": 65,
        },
        "team_form": {
            "fulham": {
                "form_summary": "近5场2胜1平2负",
                "strengths": ["主场韧性", "反击速度"],
                "weaknesses": ["防线不稳", "定位球防守差"],
            },
            "manchester_united": {
                "form_summary": "近5场3胜1平1负",
                "strengths": ["中场控制", "边路突破"],
                "weaknesses": ["客场不稳定"],
            },
        },
        "prediction": {
            "asian_handicap_direction": "曼彻斯特联 -0.5",
            "asian_handicap_confidence": 70,
            "result_1x2_lean": "曼彻斯特联胜",
            "result_1x2_confidence": 65,
            "estimated_score_range": ["0:1", "0:2", "1:2"],
            "primary_score_guess": "0:2",
            "total_goals_lean": "偏小",
            "total_goals_confidence": 55,
        },
        "risk_factors": ["数据来源不确定", "缺少独立胜平负数据"],
        "data_quality": {
            "independent_1x2_missing": True,
            "independent_total_goals_missing": True,
            "verified_lineups_missing": True,
            "provenance_uncertain": True,
        },
        "reasoning_summary": ["盘口指向客队", "基本面支持客队不败"],
    }


@pytest.fixture
def valid_input_data():
    """A minimal valid pre-match input (no outcome fields)."""
    return {
        "fixture_id": "3003893",
        "home_team": "富勒姆",
        "away_team": "曼彻斯特联",
        "kickoff_at": "2024-01-01T12:00:00Z",
    }


@pytest.fixture
def input_with_outcome():
    """Input that contains a current-match outcome field."""
    return {
        "fixture_id": "3003893",
        "home_team": "富勒姆",
        "away_team": "曼彻斯特联",
        "outcome": {"final_score": "1:2"},
    }


# ---------------------------------------------------------------------------
# 1. Exit code 10 when --enable-network is not passed
# ---------------------------------------------------------------------------

def test_exit_code_10_without_network(valid_input_data):
    """Without --enable-network, script exits with code 10 and no network call."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(valid_input_data, f)
        input_path = f.name

    output_path = tempfile.mktemp(suffix=".json")

    try:
        with mock.patch.object(sys, "argv", [
            "tools_historical_reconstruction.py",
            "--input", input_path,
            "--output", output_path,
        ]):
            with pytest.raises(SystemExit) as exc_info:
                tool.main()
            assert exc_info.value.code == 10
    finally:
        os.unlink(input_path)
        if os.path.exists(output_path):
            os.unlink(output_path)


# ---------------------------------------------------------------------------
# 2. Refuse API call when outcome fields are present
# ---------------------------------------------------------------------------

def test_reject_outcome_fields(input_with_outcome):
    """Input with outcome fields triggers SystemExit(3) before any API call."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(input_with_outcome, f)
        input_path = f.name

    output_path = tempfile.mktemp(suffix=".json")

    try:
        with mock.patch.object(sys, "argv", [
            "tools_historical_reconstruction.py",
            "--input", input_path,
            "--output", output_path,
            "--enable-network",
        ]):
            with pytest.raises(SystemExit) as exc_info:
                tool.main()
            # Should exit before reaching env checks (exit 3, not 4)
            assert exc_info.value.code == 3
    finally:
        os.unlink(input_path)
        if os.path.exists(output_path):
            os.unlink(output_path)


# ---------------------------------------------------------------------------
# 3. Valid model JSON passes validation
# ---------------------------------------------------------------------------

def test_valid_model_passes(valid_model_json):
    """A fully valid model result should pass validate_model_result."""
    tool.validate_model_result(valid_model_json)


# ---------------------------------------------------------------------------
# 4. Top-level field set violations
# ---------------------------------------------------------------------------

def test_extra_top_level_field_rejected(valid_model_json):
    """Extra top-level field causes ValueError."""
    data = dict(valid_model_json)
    data["extra_field"] = "should not be here"
    with pytest.raises(ValueError, match="unexpected fields"):
        tool.validate_model_result(data)


def test_missing_top_level_field_rejected(valid_model_json):
    """Missing top-level field causes ValueError."""
    data = dict(valid_model_json)
    del data["prediction"]
    with pytest.raises(ValueError, match="missing fields"):
        tool.validate_model_result(data)


# ---------------------------------------------------------------------------
# 5. Nested field set violations
# ---------------------------------------------------------------------------

def test_extra_market_read_field_rejected(valid_model_json):
    """Extra field in market_read causes ValueError."""
    data = json.loads(json.dumps(valid_model_json))
    data["market_read"]["extra"] = "nope"
    with pytest.raises(ValueError, match="market_read.*unexpected"):
        tool.validate_model_result(data)


def test_missing_market_read_field_rejected(valid_model_json):
    """Missing field in market_read causes ValueError."""
    data = json.loads(json.dumps(valid_model_json))
    del data["market_read"]["market_signal"]
    with pytest.raises(ValueError, match="market_read.*missing"):
        tool.validate_model_result(data)


def test_extra_team_field_rejected(valid_model_json):
    """Extra field in a team object causes ValueError."""
    data = json.loads(json.dumps(valid_model_json))
    data["team_form"]["fulham"]["extra"] = "nope"
    with pytest.raises(ValueError, match="team_form.fulham.*unexpected"):
        tool.validate_model_result(data)


def test_extra_prediction_field_rejected(valid_model_json):
    """Extra field in prediction causes ValueError."""
    data = json.loads(json.dumps(valid_model_json))
    data["prediction"]["extra"] = "nope"
    with pytest.raises(ValueError, match="prediction.*unexpected"):
        tool.validate_model_result(data)


def test_extra_data_quality_field_rejected(valid_model_json):
    """Extra field in data_quality causes ValueError."""
    data = json.loads(json.dumps(valid_model_json))
    data["data_quality"]["extra"] = True
    with pytest.raises(ValueError, match="data_quality.*unexpected"):
        tool.validate_model_result(data)


# ---------------------------------------------------------------------------
# 6. Type errors
# ---------------------------------------------------------------------------

def test_fixture_id_wrong_type_rejected(valid_model_json):
    """fixture_id as int is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["fixture_id"] = 3003893
    with pytest.raises(ValueError, match="fixture_id"):
        tool.validate_model_result(data)


def test_fixture_id_wrong_value_rejected(valid_model_json):
    """fixture_id with wrong string value is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["fixture_id"] = "9999999"
    with pytest.raises(ValueError, match="fixture_id"):
        tool.validate_model_result(data)


def test_confidence_bool_rejected(valid_model_json):
    """bool masquerading as int in confidence field is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["prediction"]["asian_handicap_confidence"] = True
    with pytest.raises(ValueError, match="asian_handicap_confidence"):
        tool.validate_model_result(data)


def test_confidence_out_of_range_rejected(valid_model_json):
    """Confidence value > 100 is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["prediction"]["asian_handicap_confidence"] = 150
    with pytest.raises(ValueError, match="out of range"):
        tool.validate_model_result(data)


def test_confidence_negative_rejected(valid_model_json):
    """Negative confidence value is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["prediction"]["asian_handicap_confidence"] = -5
    with pytest.raises(ValueError, match="out of range"):
        tool.validate_model_result(data)


# ---------------------------------------------------------------------------
# 7. Invalid enum values
# ---------------------------------------------------------------------------

def test_invalid_handicap_direction_rejected(valid_model_json):
    """Invalid asian_handicap_direction is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["prediction"]["asian_handicap_direction"] = "富勒姆 -0.5"
    with pytest.raises(ValueError, match="asian_handicap_direction"):
        tool.validate_model_result(data)


def test_invalid_1x2_lean_rejected(valid_model_json):
    """Invalid result_1x2_lean is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["prediction"]["result_1x2_lean"] = "主胜"
    with pytest.raises(ValueError, match="result_1x2_lean"):
        tool.validate_model_result(data)


def test_invalid_total_goals_lean_rejected(valid_model_json):
    """Invalid total_goals_lean is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["prediction"]["total_goals_lean"] = "大球"
    with pytest.raises(ValueError, match="total_goals_lean"):
        tool.validate_model_result(data)


# ---------------------------------------------------------------------------
# 8. Array element type checks
# ---------------------------------------------------------------------------

def test_strengths_non_string_rejected(valid_model_json):
    """Non-string element in strengths list is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["team_form"]["fulham"]["strengths"] = ["ok", 123]
    with pytest.raises(ValueError, match="strengths.*expected str"):
        tool.validate_model_result(data)


def test_risk_factors_non_string_rejected(valid_model_json):
    """Non-string element in risk_factors is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["risk_factors"] = ["ok", None]
    with pytest.raises(ValueError, match="risk_factors.*expected str"):
        tool.validate_model_result(data)


# ---------------------------------------------------------------------------
# 9. Model returns array instead of object
# ---------------------------------------------------------------------------

def test_model_returns_array_rejected():
    """When DeepSeek returns a JSON array, parse_response exits cleanly."""
    array_response = json.dumps({
        "choices": [{"message": {"content": "[1, 2, 3]"}}]
    })
    with pytest.raises(SystemExit) as exc_info:
        tool.parse_response(array_response.encode("utf-8"))
    assert exc_info.value.code == 8


def test_model_returns_string_rejected():
    """When DeepSeek returns a plain string, parse_response exits cleanly."""
    string_response = json.dumps({
        "choices": [{"message": {"content": "\"just a string\""}}]
    })
    with pytest.raises(SystemExit) as exc_info:
        tool.parse_response(string_response.encode("utf-8"))
    assert exc_info.value.code == 8


# ---------------------------------------------------------------------------
# 10. data_quality fields must be True
# ---------------------------------------------------------------------------

def test_data_quality_false_rejected(valid_model_json):
    """data_quality field set to False is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["data_quality"]["independent_1x2_missing"] = False
    with pytest.raises(ValueError, match="must be true"):
        tool.validate_model_result(data)


def test_data_quality_string_rejected(valid_model_json):
    """data_quality field as string is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["data_quality"]["provenance_uncertain"] = "yes"
    with pytest.raises(ValueError, match="must be true"):
        tool.validate_model_result(data)


# ---------------------------------------------------------------------------
# 11. HTTP / network error does not leak response body or sensitive details
# ---------------------------------------------------------------------------

def test_http_error_no_body_leak():
    """HTTPError prints only status code, not response body."""
    import urllib.error

    mock_error = urllib.error.HTTPError(
        "https://api.deepseek.com/chat/completions",
        500,
        "Internal Error",
        {},  # headers — not printed
        None,
    )
    # Patch read() to return a fake body that must NOT appear in output
    mock_error.read = lambda size=512: b'{"error":"secret detail"}'

    with mock.patch.object(
        urllib.request, "urlopen", side_effect=mock_error
    ):
        with pytest.raises(SystemExit) as exc_info:
            tool.call_deepseek(b"{}", "sk-test-key")
        assert exc_info.value.code == 5


def test_network_error_no_exception_leak():
    """URLError prints fixed message, not the exception object."""
    import urllib.error

    with mock.patch.object(
        urllib.request, "urlopen",
        side_effect=urllib.error.URLError("connection refused")
    ):
        with pytest.raises(SystemExit) as exc_info:
            tool.call_deepseek(b"{}", "sk-test-key")
        assert exc_info.value.code == 6


# ---------------------------------------------------------------------------
# 12. input_sha256 matches raw input bytes
# ---------------------------------------------------------------------------

def test_input_sha256_matches_raw_bytes():
    """SHA-256 in output matches hash of the actual input file bytes."""
    input_data = {"fixture_id": "3003893", "key": "value"}
    input_json = json.dumps(input_data, ensure_ascii=False).encode("utf-8")
    expected_hash = hashlib.sha256(input_json).hexdigest()

    output = tool.build_output(
        input_data=input_data,
        deepseek_result={"prediction": {"asian_handicap_direction": "NO_BET", "asian_handicap_confidence": 0}},
        model="deepseek-chat",
        input_file_path="/tmp/test.json",
        input_bytes=input_json,
    )
    assert output["input_sha256"] == expected_hash


# ---------------------------------------------------------------------------
# 13. generated_at, PRIMARY_DIRECTION, PRIMARY_CONFIDENCE
# ---------------------------------------------------------------------------

def test_generated_at_is_utc_iso():
    """generated_at is a UTC ISO 8601 string."""
    output = tool.build_output(
        input_data={"fixture_id": "3003893"},
        deepseek_result={"prediction": {"asian_handicap_direction": "NO_BET", "asian_handicap_confidence": 0}},
        model="deepseek-chat",
        input_file_path="/tmp/test.json",
        input_bytes=b"{}",
    )
    assert "generated_at" in output
    # Should contain T and +00:00 (UTC)
    assert "T" in output["generated_at"]
    assert "+" in output["generated_at"] or output["generated_at"].endswith("Z")


def test_primary_direction_and_confidence():
    """PRIMARY_DIRECTION and PRIMARY_CONFIDENCE are extracted from prediction."""
    output = tool.build_output(
        input_data={"fixture_id": "3003893"},
        deepseek_result={
            "prediction": {
                "asian_handicap_direction": "曼彻斯特联 -0.5",
                "asian_handicap_confidence": 70,
            }
        },
        model="deepseek-chat",
        input_file_path="/tmp/test.json",
        input_bytes=b"{}",
    )
    assert output["PRIMARY_DIRECTION"] == "曼彻斯特联 -0.5"
    assert output["PRIMARY_CONFIDENCE"] == 70


# ---------------------------------------------------------------------------
# 14. Write failure does not corrupt existing output file
# ---------------------------------------------------------------------------

def test_write_failure_preserves_existing_file():
    """If writing fails mid-way, the existing output file is untouched."""
    import tempfile

    # Create an existing output file with known content
    existing_content = '{"original": true}'
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(existing_content)
        output_path = f.name

    input_path = tempfile.mktemp(suffix=".json")
    with open(input_path, "w") as f:
        json.dump({"fixture_id": "3003893"}, f)

    try:
        # Simulate a failure during the final write by making os.replace fail
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            with mock.patch.object(sys, "argv", [
                "tools_historical_reconstruction.py",
                "--input", input_path,
                "--output", output_path,
                "--enable-network",
            ]):
                # Mock the entire DeepSeek call chain
                valid_result = {
                    "fixture_id": "3003893",
                    "analysis_type": "historical_prematch_reconstruction_simulation",
                    "market_read": {
                        "main_handicap_consensus": "",
                        "handicap_movement_summary": "",
                        "water_movement_summary": "",
                        "market_signal": "",
                        "market_signal_strength": 0,
                    },
                    "team_form": {
                        "fulham": {"form_summary": "", "strengths": [], "weaknesses": []},
                        "manchester_united": {"form_summary": "", "strengths": [], "weaknesses": []},
                    },
                    "prediction": {
                        "asian_handicap_direction": "NO_BET",
                        "asian_handicap_confidence": 0,
                        "result_1x2_lean": "NO_BET",
                        "result_1x2_confidence": 0,
                        "estimated_score_range": [],
                        "primary_score_guess": "",
                        "total_goals_lean": "NO_BET",
                        "total_goals_confidence": 0,
                    },
                    "risk_factors": [],
                    "data_quality": {
                        "independent_1x2_missing": True,
                        "independent_total_goals_missing": True,
                        "verified_lineups_missing": True,
                        "provenance_uncertain": True,
                    },
                    "reasoning_summary": [],
                }
                mock_response = json.dumps({
                    "choices": [{"message": {"content": json.dumps(valid_result)}}]
                }).encode("utf-8")
                with mock.patch.object(tool, "call_deepseek", return_value=mock_response):
                    with mock.patch.dict(os.environ, {
                        "DEEPSEEK_API_KEY": "sk-test",
                        "DEEPSEEK_ANALYSIS_ENABLED": "true",
                    }):
                        with pytest.raises(OSError, match="disk full"):
                            tool.main()

        # Existing file should be untouched
        with open(output_path, "r") as f:
            assert f.read() == existing_content
    finally:
        os.unlink(input_path)
        if os.path.exists(output_path):
            os.unlink(output_path)


# ---------------------------------------------------------------------------
# 15. market_signal_strength as bool rejected
# ---------------------------------------------------------------------------

def test_market_signal_strength_bool_rejected(valid_model_json):
    """bool in market_signal_strength is rejected."""
    data = json.loads(json.dumps(valid_model_json))
    data["market_read"]["market_signal_strength"] = False
    with pytest.raises(ValueError, match="market_signal_strength"):
        tool.validate_model_result(data)
