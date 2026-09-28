"""Operator-only one-request DeepSeek connectivity diagnostic.

No production DB or website route. Requires two independent opt-ins and a
secret supplied via a private prompt or existing process environment.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://api.deepseek.com/chat/completions"
MAX_RESPONSE_BYTES = 8192


class ConnectivityError(Exception):
    pass


def probe(*, enabled: bool, environment: dict | None = None, transport=None, secret_reader=None) -> dict:
    """Send at most one short request after explicit authorization."""
    env = os.environ if environment is None else environment
    if not enabled or env.get("DEEPSEEK_ANALYSIS_ENABLED") != "true":
        raise ConnectivityError("diagnostic disabled; both opt-ins are required")
    model = env.get("DEEPSEEK_MODEL", "deepseek-chat")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", model):
        raise ConnectivityError("invalid model name")
    key = env.get("DEEPSEEK_API_KEY")
    if not key:
        reader = secret_reader or getpass.getpass
        key = reader("DeepSeek API key (hidden; not saved): ")
    if not isinstance(key, str) or not (1 <= len(key) <= 512) or any(c.isspace() for c in key):
        raise ConnectivityError("missing or malformed API key")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "请只回复：连接成功"}],
        "max_tokens": 24,
        "temperature": 0,
    }, ensure_ascii=False).encode("utf-8")
    sender = transport or _send_once
    try:
        response = sender(ENDPOINT, body, key, 12)
        if not isinstance(response, bytes) or len(response) > MAX_RESPONSE_BYTES:
            raise ConnectivityError("API response invalid or too large")
        parsed = json.loads(response)
        content = parsed["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ConnectivityError("API reply contains no text")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
            ValueError, TypeError, KeyError, IndexError) as exc:
        # Avoid propagating provider or transport exception text: it can contain
        # details from the request or credential-bearing headers.
        raise ConnectivityError("request failed; check key, balance, model and HTTPS connectivity") from None
    return {"status": "connected", "model": model, "request_count": 1,
            "note": "仅验证最小模型响应，不代表足球分析或预测功能已上线。"}


def _send_once(url: str, body: bytes, key: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ConnectivityError("API response too large")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="One-shot, operator-only DeepSeek connectivity check")
    parser.add_argument("--enable-network", action="store_true", help="Authorize one API request")
    args = parser.parse_args(argv)
    try:
        result = probe(enabled=args.enable_network)
    except ConnectivityError as exc:
        print("CONNECTIVITY_FAILED: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
