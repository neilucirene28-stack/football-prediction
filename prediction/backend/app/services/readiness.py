"""Conservative, read-only match data coverage report.

This is NOT a prediction or an assessment of match outcome. It operates only on
fields already returned by the public match detail endpoint, not raw payloads.
"""
from collections.abc import Mapping

MODULE_LABELS = {
    "analysis": "球队分析", "asia": "亚洲让球", "total_goals": "总进球",
    "wdl": "胜平负补充资料", "corners": "角球", "live_analysis": "首发与现场分析",
}
_INVALID_STATES = {"failed", "error", "missing", "not_collected", "pending_review"}
_META = {"status", "reason", "error", "message", "capturedAt", "captured_at",
         "collected_at", "source", "来源网址", "采集时间", "数据状态"}


def _actual(value):
    """Do not treat a success flag, failed fetch, or unverified nested table as data."""
    if not isinstance(value, Mapping):
        return False
    if str(value.get("status", "")).lower() in _INVALID_STATES:
        return False
    for key, item in value.items():
        if key not in _META and isinstance(item, (str, int, float)) and not isinstance(item, bool) and str(item).strip():
            return True
    return False


def coverage_report(detail: Mapping) -> dict:
    modules = detail.get("modules") or {}
    if not isinstance(modules, Mapping):
        modules = {}
    missing = []
    present = []
    for key, label in MODULE_LABELS.items():
        if _actual(modules.get(key)):
            present.append({"key": key, "label": label})
        else:
            entry = modules.get(key)
            state = str(entry.get("status", "")) if isinstance(entry, Mapping) else ""
            missing.append({"key": key, "label": label,
                            "reason": "采集失败" if state in {"failed", "error"} else
                                      "待人工核实" if state == "pending_review" else "缺少可确认字段"})
    odds = detail.get("sporttery_odds")
    valid_odds = sum(1 for o in odds if isinstance(o, Mapping) and
                     all(o.get(k) not in (None, "") for k in ("market", "home_odds", "draw_odds", "away_odds"))) if isinstance(odds, list) else 0
    if not valid_odds:
        missing.append({"key": "sporttery_odds", "label": "竞彩赔率", "reason": "没有完整的三项赔率"})
    if not detail.get("kickoff_at"):
        missing.append({"key": "kickoff_at", "label": "开球时间", "reason": "无法确认比赛时间"})
    return {
        "status": "incomplete" if missing else "fields_present_unverified",
        "module_fields_present": present,
        "missing": missing,
        "verified_sporttery_rows": valid_odds,
        "notice": "仅统计可显示字段是否存在；不代表数据真实性、时效性或足以预测。未运行AI预测。",
    }
