"""Operator-only one-shot analysis of an independently reviewed future fixture.

The website remains unchanged. No DB, browser route, saved key, or implicit retry.
Run from backend/: DEEPSEEK_ANALYSIS_ENABLED=true python3 tools_fixture_analysis.py --input /private/fixture.json --enable-network
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from app.services.deepseek_analysis import (
    AnalysisError, MAX_INPUT_BYTES, analyze_with_deepseek, prepare_analysis_input,
)


class OperatorError(ValueError):
    pass


def read_fixture(path: str) -> dict:
    file = Path(path)
    if not file.is_file() or file.is_symlink():
        raise OperatorError("fixture must be a regular, non-symlink local file")
    if file.stat().st_size > MAX_INPUT_BYTES:
        raise OperatorError("fixture file too large")
    try:
        raw = file.read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            raise OperatorError("fixture file too large")
        value = json.loads(raw)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise OperatorError("fixture JSON unavailable or invalid") from None
    if not isinstance(value, dict):
        raise OperatorError("fixture must be a JSON object")
    return value


def run_fixture(path: str, *, enabled: bool, environ=None, reader=None, transport=None, now=None) -> dict:
    """Validate before prompting. Invoke exactly once only when both gates are on."""
    env = os.environ if environ is None else environ
    if not enabled or env.get("DEEPSEEK_ANALYSIS_ENABLED") != "true":
        raise OperatorError("disabled: --enable-network and DEEPSEEK_ANALYSIS_ENABLED=true are both required")
    payload = read_fixture(path)
    prepare_analysis_input(payload, now=now)  # fail before asking for secret
    key = env.get("DEEPSEEK_API_KEY", "")
    if not key:
        prompt = getpass.getpass if reader is None else reader
        key = prompt("DeepSeek API key (hidden; not saved): ")
    if not isinstance(key, str) or not (1 <= len(key) <= 512) or any(c.isspace() for c in key):
        raise OperatorError("API key missing or invalid")
    # Keep key only in this process, never in a command argument, file, or result.
    # Existing analysis module reads its key from the process environment.
    old_key = os.environ.get("DEEPSEEK_API_KEY")
    old_gate = os.environ.get("DEEPSEEK_ANALYSIS_ENABLED")
    try:
        os.environ["DEEPSEEK_API_KEY"] = key
        os.environ["DEEPSEEK_ANALYSIS_ENABLED"] = "true"
        result = analyze_with_deepseek(payload, enabled=True, transport=transport, now=now)
        return result
    finally:
        if old_key is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = old_key
        if old_gate is None:
            os.environ.pop("DEEPSEEK_ANALYSIS_ENABLED", None)
        else:
            os.environ["DEEPSEEK_ANALYSIS_ENABLED"] = old_gate


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-time, terminal-only future fixture DeepSeek analysis")
    parser.add_argument("--input", required=True, help="Locally reviewed fixture JSON path; NOT an API key")
    parser.add_argument("--enable-network", action="store_true", help="Explicitly permit one network request")
    args = parser.parse_args(argv)
    try:
        result = run_fixture(args.input, enabled=args.enable_network)
    except (OperatorError, AnalysisError):
        print("ANALYSIS_UNAVAILABLE: check fixture, both opt-ins, key, model, balance or network; no result saved", file=sys.stderr)
        return 2
    # Only structured, validated model fields, no key or original private fixture JSON.
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
