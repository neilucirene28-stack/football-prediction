"""Offline-by-default DeepSeek analysis of an explicitly supplied match detail JSON.

No database writes, no public route, no secret in a URL, and no calls unless the
operator explicitly enables the CLI and supplies an API key in its environment.
"""
import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

API_URL = "https://api.deepseek.com/chat/completions"
MAX_INPUT_BYTES = 48_000
MAX_RESPONSE_BYTES = 32_000
ALLOWED_MARKETS = {"wdl", "nwdl"}


class PredictionError(ValueError):
    pass


def _odds(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not Decimal("1") < number <= Decimal("1000"):
            return None
        return str(number)
    except (InvalidOperation, TypeError, ValueError):
        return None


def prepare_prediction_input(detail: Mapping, now=None) -> dict:
    """Allowlist *verified-shape* fields only; metadata is not independent evidence.

    Historical fixtures and fixtures lacking pre-match team data are not sent to
    the model. Merely having one odds row must never unlock prediction.
    """
    if not isinstance(detail, Mapping):
        raise PredictionError("match detail must be a JSON object")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise PredictionError("now must have a timezone")
    kickoff_raw = detail.get("kickoff_at")
    try:
        kickoff = datetime.fromisoformat(kickoff_raw.replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            raise ValueError("missing timezone")
    except (TypeError, AttributeError, ValueError) as exc:
        raise PredictionError("missing or invalid kickoff time") from exc
    if kickoff.astimezone(timezone.utc) <= now.astimezone(timezone.utc):
        raise PredictionError("historical or already-started match: no pre-match prediction")
    home, away = detail.get("home_team"), detail.get("away_team")
    if not all(isinstance(x, str) and 1 <= len(x.strip()) <= 120 for x in (home, away)):
        raise PredictionError("missing team identity")
    if home.strip() == away.strip():
        raise PredictionError("teams must differ")
    rows = []
    for row in detail.get("sporttery_odds") or []:
        if not isinstance(row, Mapping) or row.get("market") not in ALLOWED_MARKETS:
            continue
        values = [_odds(row.get(k)) for k in ("home_odds", "draw_odds", "away_odds")]
        if any(v is None for v in values):
            continue
        observed = row.get("observed_at")
        try:
            stamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp > now or stamp > kickoff:
                continue
        except (AttributeError, TypeError, ValueError):
            continue
        rows.append({"market": row["market"], "home_odds": values[0],
                     "draw_odds": values[1], "away_odds": values[2],
                     "observed_at": stamp.isoformat()})
    # Source snapshots can contain HTTP failures, untrusted nested tables, or
    # past observations. This phase does not treat them as vetted analysis.
    if not rows:
        raise PredictionError("no valid pre-match three-way odds observations")
    raise PredictionError("prediction withheld: independent pre-match team/form and lineup evidence is not yet validated")


def validate_model_result(value) -> dict:
    """Validate a *non-predictive* structured analysis; never accept invented odds."""
    if not isinstance(value, dict) or value.get("status") != "insufficient_data":
        raise PredictionError("model must return insufficient_data for current data contract")
    summary = value.get("summary")
    if not isinstance(summary, str) or not 1 <= len(summary) <= 800:
        raise PredictionError("invalid model summary")
    return {"status": "insufficient_data", "summary": summary,
            "predictions": None, "notice": "未生成胜平负、让球、大小球或比分预测。"}


def analyze_insufficient_data(detail: Mapping, *, enabled=False, now=None) -> dict:
    """Guardrail-only preview; API call is intentionally not enabled this phase."""
    if enabled:
        raise PredictionError("DeepSeek network calls are not approved in this phase")
    try:
        prepare_prediction_input(detail, now=now)
    except PredictionError as exc:
        return {"status": "insufficient_data", "reason": str(exc),
                "predictions": None, "api_called": False}
    return {"status": "insufficient_data", "reason": "prediction disabled",
            "predictions": None, "api_called": False}
