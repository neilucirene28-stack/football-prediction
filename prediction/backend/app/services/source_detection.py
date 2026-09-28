"""Conservative detection for the two known collector envelopes.

Unknown and ambiguous payloads are never mapped to a supported source.
No data is written by this module.
"""

SUPPORTED = {"xiaodianhuo", "titan007"}


def detect_source(payload):
    if not isinstance(payload, dict):
        return None
    # A XDH file must have both its fixed report type and valid match envelope.
    xdh = (
        payload.get("reportType") == "XDH_JCZQ_BATCH"
        and isinstance(payload.get("matches"), list)
        and bool(payload["matches"])
        and all(isinstance(e, dict) and isinstance(e.get("match"), dict)
                and bool(str(e["match"].get("match_id") or "").strip())
                for e in payload["matches"])
    )
    titan = (
        isinstance(payload.get("比赛信息"), dict)
        and bool(str(payload["比赛信息"].get("比赛ID") or "").strip())
        and isinstance(payload.get("数据状态"), dict)
        and any(k in payload for k in ("分析", "亚让", "胜平负", "角球", "现场分析"))
    )
    if xdh == titan:  # unknown OR ambiguous
        return None
    return "xiaodianhuo" if xdh else "titan007"


def resolve_source(payload, declared=None):
    detected = detect_source(payload)
    if declared and declared not in SUPPORTED:
        raise ValueError("unsupported source")
    if detected is None:
        raise ValueError("unknown or ambiguous file structure")
    if declared and declared != detected:
        raise ValueError("declared source does not match file structure")
    return detected
