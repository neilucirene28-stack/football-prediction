"""Strict, positive identification of the two supported collection file formats.

File names, absence of reportType, and team-name similarity are never evidence
of a source. Unknown and ambiguous payloads must be reviewed, not normalized.
"""
from typing import Any


class SourceValidationError(ValueError):
    pass


def identify_source(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise SourceValidationError("top-level JSON must be an object")

    # XDH_JCZQ_BATCH is the actual batch export reportType. Requiring a
    # nonempty per-entry source ID prevents an unrelated report being accepted.
    is_xdh = (
        payload.get("reportType") == "XDH_JCZQ_BATCH"
        and isinstance(payload.get("matches"), list)
        and bool(payload["matches"])
        and all(
            isinstance(entry, dict)
            and isinstance(entry.get("match"), dict)
            and str(entry["match"].get("match_id") or "").strip()
            and isinstance(entry.get("details", {}), dict)
            for entry in payload["matches"]
        )
    )

    info = payload.get("比赛信息")
    states = payload.get("数据状态")
    modules = ("分析", "亚让", "总进球", "胜平负", "角球", "现场分析")
    is_titan = (
        isinstance(info, dict)
        and bool(str(info.get("比赛ID") or "").strip())
        and isinstance(states, dict)
        and any(name in states for name in modules)
        and any(name in payload and isinstance(payload[name], dict) for name in modules)
    )

    if is_xdh and not is_titan:
        return "xiaodianhuo"
    if is_titan and not is_xdh:
        return "titan007"
    raise SourceValidationError("unrecognized or ambiguous source; keep for manual review")


def validate_source(payload: Any, declared_source: str | None = None) -> str:
    detected = identify_source(payload)
    if declared_source not in (None, "unknown", detected):
        raise SourceValidationError("declared source conflicts with verified file structure")
    return detected
