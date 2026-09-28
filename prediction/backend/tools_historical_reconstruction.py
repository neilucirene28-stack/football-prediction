"""Historical pre-match reconstruction simulation — local operator CLI only.
Reads an independently audited pre-match JSON, validates it contains no
current-match outcome data, then calls DeepSeek for a blinded reconstruction.
Usage (from backend/):
  python3 tools_historical_reconstruction.py \
    --input ../audit_output/model_input_prematch.json \
    --output ../audit_output/prediction_blinded_reconstruction.json \
    --enable-network
Security:
  - Reads DEEPSEEK_API_KEY / DEEPSEEK_ANALYSIS_ENABLED / DEEPSEEK_MODEL
    from process environment only; never reads .env.
  - Never prints the API key, its length, prefix, suffix, or the
    Authorization header.
  - Refuses to call the API if current-match outcome fields are detected.
  - Does not read outcome_only.json, databases, result websites, or
    raw Titan007 files.
  - Does not import or call any existing deepseek_analysis module.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_ENDPOINT = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
MAX_OUTPUT_BYTES = 16_384
REQUEST_TIMEOUT = 25  # seconds
FIXTURE_ID = "3003893"

# Fields whose presence in the input would indicate current-match outcome
# data has leaked into the pre-match snapshot.
OUTCOME_INDICATOR_FIELDS: List[str] = [
    "outcome",
    "final_score",
    "current_match_result",
    "full_time_result",
    "最终比分",
    "本场赛果",
    "postmatch",
    "赛后",
]

# ---------------------------------------------------------------------------
# Allowed field sets for strict validation
# ---------------------------------------------------------------------------
ALLOWED_TOP_KEYS = {
    "fixture_id", "analysis_type", "market_read", "team_form",
    "prediction", "risk_factors", "data_quality", "reasoning_summary",
}

ALLOWED_MARKET_READ_KEYS = {
    "main_handicap_consensus",
    "handicap_movement_summary",
    "water_movement_summary",
    "market_signal",
    "market_signal_strength",
}

ALLOWED_TEAM_FORM_KEYS = {"fulham", "manchester_united"}

ALLOWED_TEAM_KEYS = {"form_summary", "strengths", "weaknesses"}

ALLOWED_PREDICTION_KEYS = {
    "asian_handicap_direction",
    "asian_handicap_confidence",
    "result_1x2_lean",
    "result_1x2_confidence",
    "estimated_score_range",
    "primary_score_guess",
    "total_goals_lean",
    "total_goals_confidence",
}

ALLOWED_DATA_QUALITY_KEYS = {
    "independent_1x2_missing",
    "independent_total_goals_missing",
    "verified_lineups_missing",
    "provenance_uncertain",
}

VALID_HANDICAP_DIRECTIONS = {
    "富勒姆 +0.5", "富勒姆 +0.25",
    "曼彻斯特联 -0.5", "曼彻斯特联 -0.25",
    "NO_BET",
}

VALID_1X2_LEANS = {"富勒姆胜", "平", "曼彻斯特联胜", "NO_BET"}

VALID_TOTAL_GOALS_LEANS = {"偏大", "偏小", "中性", "NO_BET"}

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def _deep_has_key(obj: Any, target: str) -> bool:
    """Recursively check whether *target* appears as a dict key anywhere."""
    if isinstance(obj, dict):
        if target in obj:
            return True
        return any(_deep_has_key(v, target) for v in obj.values())
    if isinstance(obj, list):
        return any(_deep_has_key(item, target) for item in obj)
    return False


def validate_input(data: Dict[str, Any]) -> None:
    """Raise SystemExit if the input fails safety checks."""
    fixture = data.get("fixture_id")
    if str(fixture) != FIXTURE_ID:
        print(
            f"fixture_id mismatch: expected {FIXTURE_ID}, got {fixture}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    for field in OUTCOME_INDICATOR_FIELDS:
        if _deep_has_key(data, field):
            print(
                f"current-match outcome field detected: '{field}'. "
                "Aborting to prevent data leakage.",
                file=sys.stderr,
            )
            raise SystemExit(3)


def _read_env_required(name: str) -> str:
    """Read a required env var; exit with a clear message if missing."""
    value = os.environ.get(name, "")
    if not value:
        print(f"{name} is not set in the process environment.", file=sys.stderr)
        raise SystemExit(4)
    return value


# ---------------------------------------------------------------------------
# DeepSeek request
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = (
    "这是历史比赛的赛前数据重建模拟。"
    "你不能利用真实赛果、训练记忆中的该场结果、网络知识或任何赛后信息。"
    "只能依据提供的 JSON。"
    "数据来源时间存在不确定性，因此这不是正式盲测，也不是经过验证的回测。"
    "缺失数据不得补造。"
    "\n\n"
    "盘口解释规则（必须严格遵守）：\n"
    "- 富勒姆受让半球 = 富勒姆 +0.5 = 曼彻斯特联 -0.5\n"
    "- 富勒姆受让平手/半球 = 富勒姆 +0.25 = 曼彻斯特联 -0.25\n"
    "禁止反向理解。"
    "\n\n"
    "请分析以下维度：\n"
    "1. 富勒姆近况\n"
    "2. 曼彻斯特联近况\n"
    "3. 正式比赛和友谊赛差异\n"
    "4. 主客场表现\n"
    "5. 进失球走势\n"
    "6. 赛程密度\n"
    "7. 11 家明确公司盘口\n"
    "8. 初盘与即时盘\n"
    "9. 201 条盘口历史变化\n"
    "10. 水位变化\n"
    "11. 富勒姆主场受让盘口含义\n"
    "12. 香港马* 与其他公司的盘口差异\n"
    "13. 数据缺失\n"
    "14. provenance_uncertain 风险\n"
    "\n"
    "仅返回 JSON 对象，不要包含任何其他文本。"
    "\n\n"
    "返回对象必须严格遵循以下结构，不得增删字段：\n"
    "{\n"
    "  \"fixture_id\": \"3003893\",\n"
    "  \"analysis_type\": \"historical_prematch_reconstruction_simulation\",\n"
    "  \"market_read\": {\n"
    "    \"main_handicap_consensus\": \"\",\n"
    "    \"handicap_movement_summary\": \"\",\n"
    "    \"water_movement_summary\": \"\",\n"
    "    \"market_signal\": \"\",\n"
    "    \"market_signal_strength\": 0\n"
    "  },\n"
    "  \"team_form\": {\n"
    "    \"fulham\": {\n"
    "      \"form_summary\": \"\",\n"
    "      \"strengths\": [],\n"
    "      \"weaknesses\": []\n"
    "    },\n"
    "    \"manchester_united\": {\n"
    "      \"form_summary\": \"\",\n"
    "      \"strengths\": [],\n"
    "      \"weaknesses\": []\n"
    "    }\n"
    "  },\n"
    "  \"prediction\": {\n"
    "    \"asian_handicap_direction\": \"\",\n"
    "    \"asian_handicap_confidence\": 0,\n"
    "    \"result_1x2_lean\": \"\",\n"
    "    \"result_1x2_confidence\": 0,\n"
    "    \"estimated_score_range\": [],\n"
    "    \"primary_score_guess\": \"\",\n"
    "    \"total_goals_lean\": \"\",\n"
    "    \"total_goals_confidence\": 0\n"
    "  },\n"
    "  \"risk_factors\": [],\n"
    "  \"data_quality\": {\n"
    "    \"independent_1x2_missing\": true,\n"
    "    \"independent_total_goals_missing\": true,\n"
    "    \"verified_lineups_missing\": true,\n"
    "    \"provenance_uncertain\": true\n"
    "  },\n"
    "  \"reasoning_summary\": []\n"
    "}\n"
    "\n"
    "asian_handicap_direction 只能是：富勒姆 +0.5 / 富勒姆 +0.25 / 曼彻斯特联 -0.5 / 曼彻斯特联 -0.25 / NO_BET\n"
    "result_1x2_lean 只能是：富勒姆胜 / 平 / 曼彻斯特联胜 / NO_BET\n"
    "total_goals_lean 只能是：偏大 / 偏小 / 中性 / NO_BET\n"
    "market_signal_strength / asian_handicap_confidence / result_1x2_confidence / total_goals_confidence 必须是 0-100 的整数。"
)


def build_request_body(prepared: Dict[str, Any], model: str) -> bytes:
    """Build the JSON request body for DeepSeek."""
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(prepared, ensure_ascii=False),
            },
        ],
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def call_deepseek(body: bytes, api_key: str) -> bytes:
    """POST to DeepSeek and return the raw response body.

    Security: never prints API response body, request headers,
    Authorization header, or API key in error messages.
    """
    req = urllib.request.Request(
        API_ENDPOINT,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            output = resp.read(MAX_OUTPUT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Security: only print HTTP status code, never response body or headers.
        print(
            f"DeepSeek API HTTP error: status code {exc.code}",
            file=sys.stderr,
        )
        raise SystemExit(5) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        # Security: only print fixed message, never the exception object.
        print(
            "DeepSeek API network error: request failed (timeout or connectivity issue).",
            file=sys.stderr,
        )
        raise SystemExit(6) from exc
    if len(output) > MAX_OUTPUT_BYTES:
        print("DeepSeek response exceeds size limit.", file=sys.stderr)
        raise SystemExit(7)
    return output


def parse_response(raw: bytes) -> Dict[str, Any]:
    """Parse and validate the DeepSeek JSON response.

    Must confirm the returned content is a dict before any field access.
    """
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print("Failed to parse DeepSeek response as JSON.", file=sys.stderr)
        raise SystemExit(8) from exc

    if not isinstance(payload, dict):
        print(
            f"DeepSeek response is not a JSON object (got {type(payload).__name__}).",
            file=sys.stderr,
        )
        raise SystemExit(8)

    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) == 0:
        print("DeepSeek response missing 'choices' array.", file=sys.stderr)
        raise SystemExit(8)

    message = choices[0].get("message")
    if not isinstance(message, dict):
        print("DeepSeek response 'message' is not an object.", file=sys.stderr)
        raise SystemExit(8)

    content = message.get("content")
    if not isinstance(content, str):
        print("DeepSeek response 'content' is not a string.", file=sys.stderr)
        raise SystemExit(8)

    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        print("Failed to parse DeepSeek message content as JSON.", file=sys.stderr)
        raise SystemExit(8) from exc

    if not isinstance(result, dict):
        print(
            f"DeepSeek message content is not a JSON object (got {type(result).__name__}).",
            file=sys.stderr,
        )
        raise SystemExit(8)

    validate_model_result(result)
    return result


def _check_field_set(
    actual_keys: set, allowed_keys: set, context: str
) -> None:
    """Validate that actual_keys exactly matches allowed_keys."""
    extra = actual_keys - allowed_keys
    missing = allowed_keys - actual_keys
    errors = []
    if extra:
        errors.append(f"unexpected fields: {sorted(extra)}")
    if missing:
        errors.append(f"missing fields: {sorted(missing)}")
    if errors:
        raise ValueError(f"{context}: {'; '.join(errors)}")


def _check_string_list(value: Any, context: str) -> None:
    """Validate that value is a list of strings."""
    if not isinstance(value, list):
        raise ValueError(f"{context}: expected list, got {type(value).__name__}")
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(
                f"{context}[{i}]: expected str, got {type(item).__name__}"
            )


def _check_int_0_100(value: Any, context: str) -> None:
    """Validate that value is an int in [0, 100]."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(
            f"{context}: expected int (0-100), got {type(value).__name__} = {value!r}"
        )
    if value < 0 or value > 100:
        raise ValueError(f"{context}: value {value} out of range 0-100")


def validate_model_result(result: Dict[str, Any]) -> None:
    """Strictly validate the DeepSeek response structure and field values."""
    # --- Top-level field set ---
    _check_field_set(set(result.keys()), ALLOWED_TOP_KEYS, "top-level")

    # --- fixture_id must be string "3003893" ---
    if result.get("fixture_id") != FIXTURE_ID:
        raise ValueError(
            f"fixture_id must be '{FIXTURE_ID}', got {result.get('fixture_id')!r}"
        )
    if not isinstance(result["fixture_id"], str):
        raise ValueError(
            f"fixture_id must be str, got {type(result['fixture_id']).__name__}"
        )

    # --- analysis_type ---
    if result.get("analysis_type") != "historical_prematch_reconstruction_simulation":
        raise ValueError(
            f"analysis_type mismatch: {result.get('analysis_type')!r}"
        )

    # --- market_read ---
    market = result.get("market_read")
    if not isinstance(market, dict):
        raise ValueError(f"market_read: expected dict, got {type(market).__name__}")
    _check_field_set(set(market.keys()), ALLOWED_MARKET_READ_KEYS, "market_read")
    for k in ("main_handicap_consensus", "handicap_movement_summary",
              "water_movement_summary", "market_signal"):
        if not isinstance(market.get(k), str):
            raise ValueError(f"market_read.{k}: expected str")
    _check_int_0_100(market.get("market_signal_strength"), "market_read.market_signal_strength")

    # --- team_form ---
    team_form = result.get("team_form")
    if not isinstance(team_form, dict):
        raise ValueError(f"team_form: expected dict, got {type(team_form).__name__}")
    _check_field_set(set(team_form.keys()), ALLOWED_TEAM_FORM_KEYS, "team_form")

    for team_name in ("fulham", "manchester_united"):
        team = team_form.get(team_name)
        if not isinstance(team, dict):
            raise ValueError(f"team_form.{team_name}: expected dict")
        _check_field_set(set(team.keys()), ALLOWED_TEAM_KEYS, f"team_form.{team_name}")
        if not isinstance(team.get("form_summary"), str):
            raise ValueError(f"team_form.{team_name}.form_summary: expected str")
        _check_string_list(team.get("strengths"), f"team_form.{team_name}.strengths")
        _check_string_list(team.get("weaknesses"), f"team_form.{team_name}.weaknesses")

    # --- prediction ---
    prediction = result.get("prediction")
    if not isinstance(prediction, dict):
        raise ValueError(f"prediction: expected dict, got {type(prediction).__name__}")
    _check_field_set(set(prediction.keys()), ALLOWED_PREDICTION_KEYS, "prediction")

    if prediction.get("asian_handicap_direction") not in VALID_HANDICAP_DIRECTIONS:
        raise ValueError(
            f"prediction.asian_handicap_direction: invalid value "
            f"{prediction.get('asian_handicap_direction')!r}"
        )
    _check_int_0_100(prediction.get("asian_handicap_confidence"),
                     "prediction.asian_handicap_confidence")

    if prediction.get("result_1x2_lean") not in VALID_1X2_LEANS:
        raise ValueError(
            f"prediction.result_1x2_lean: invalid value "
            f"{prediction.get('result_1x2_lean')!r}"
        )
    _check_int_0_100(prediction.get("result_1x2_confidence"),
                     "prediction.result_1x2_confidence")

    _check_string_list(prediction.get("estimated_score_range"),
                       "prediction.estimated_score_range")

    if not isinstance(prediction.get("primary_score_guess"), str):
        raise ValueError("prediction.primary_score_guess: expected str")

    if prediction.get("total_goals_lean") not in VALID_TOTAL_GOALS_LEANS:
        raise ValueError(
            f"prediction.total_goals_lean: invalid value "
            f"{prediction.get('total_goals_lean')!r}"
        )
    _check_int_0_100(prediction.get("total_goals_confidence"),
                     "prediction.total_goals_confidence")

    # --- risk_factors ---
    _check_string_list(result.get("risk_factors"), "risk_factors")

    # --- data_quality ---
    dq = result.get("data_quality")
    if not isinstance(dq, dict):
        raise ValueError(f"data_quality: expected dict, got {type(dq).__name__}")
    _check_field_set(set(dq.keys()), ALLOWED_DATA_QUALITY_KEYS, "data_quality")
    for key in ALLOWED_DATA_QUALITY_KEYS:
        if dq.get(key) is not True:
            raise ValueError(
                f"data_quality.{key}: must be true, got {dq.get(key)!r}"
            )

    # --- reasoning_summary ---
    _check_string_list(result.get("reasoning_summary"), "reasoning_summary")


def build_output(
    input_data: Dict[str, Any],
    deepseek_result: Dict[str, Any],
    model: str,
    input_file_path: str,
    input_bytes: bytes,
) -> Dict[str, Any]:
    """Build the final output structure."""
    input_sha256 = hashlib.sha256(input_bytes).hexdigest()
    generated_at = datetime.now(timezone.utc).isoformat()

    prediction = deepseek_result.get("prediction", {})
    primary_direction = prediction.get("asian_handicap_direction", "")
    primary_confidence = prediction.get("asian_handicap_confidence", 0)

    return {
        "generated_at": generated_at,
        "model": model,
        "fixture_id": FIXTURE_ID,
        "analysis_type": "historical_prematch_reconstruction_simulation",
        "input_file": input_file_path,
        "input_sha256": input_sha256,
        "outcome_file_read": False,
        "source_provenance_uncertain": True,
        "prediction": deepseek_result,
        "PRIMARY_DIRECTION": primary_direction,
        "PRIMARY_CONFIDENCE": primary_confidence,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Historical pre-match reconstruction simulation"
    )
    parser.add_argument(
        "--input",
        default="/opt/football-prediction/audit_output/model_input_prematch.json",
        help="Path to audited pre-match JSON input",
    )
    parser.add_argument(
        "--output",
        default="/opt/football-prediction/audit_output/prediction_blinded_reconstruction.json",
        help="Path for the reconstruction output",
    )
    parser.add_argument(
        "--enable-network",
        action="store_true",
        help="Required to actually call the DeepSeek API",
    )
    args = parser.parse_args()

    # --- 1. Read input file (raw bytes for SHA-256) ---
    try:
        with open(args.input, "rb") as f:
            input_bytes = f.read()
    except (OSError, IOError) as exc:
        print(f"Failed to read input file: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    try:
        input_data = json.loads(input_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print("Input file is not valid JSON.", file=sys.stderr)
        raise SystemExit(1) from exc

    if not isinstance(input_data, dict):
        print("Input JSON is not an object.", file=sys.stderr)
        raise SystemExit(1)

    # --- 2. Safety validation ---
    validate_input(input_data)

    # --- 3. Network gate ---
    if not args.enable_network:
        print(
            "Network not enabled. Use --enable-network to call DeepSeek API.",
            file=sys.stderr,
        )
        raise SystemExit(10)

    # --- 4. Environment check ---
    enabled = os.environ.get("DEEPSEEK_ANALYSIS_ENABLED", "")
    if enabled != "true":
        print("DEEPSEEK_ANALYSIS_ENABLED must be 'true'.", file=sys.stderr)
        raise SystemExit(4)
    api_key = _read_env_required("DEEPSEEK_API_KEY")
    if len(api_key) > 512 or any(c.isspace() for c in api_key):
        print("DEEPSEEK_API_KEY is malformed.", file=sys.stderr)
        raise SystemExit(4)
    model = os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", model):
        print("DEEPSEEK_MODEL is invalid.", file=sys.stderr)
        raise SystemExit(4)

    # --- 5. Call DeepSeek ---
    body = build_request_body(input_data, model)
    raw_response = call_deepseek(body, api_key)
    deepseek_result = parse_response(raw_response)

    # --- 6. Write output (atomic: temp file then rename) ---
    output = build_output(input_data, deepseek_result, model, args.input, input_bytes)
    output_dir = os.path.dirname(args.output)
    os.makedirs(output_dir, exist_ok=True)

    # Write to a temporary file in the same directory, then atomically replace.
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", prefix=".reconstruction_", dir=output_dir
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, args.output)
    except Exception:
        # Clean up temp file on failure; do not corrupt existing output.
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    print(f"Reconstruction written to {args.output}")


if __name__ == "__main__":
    main()
