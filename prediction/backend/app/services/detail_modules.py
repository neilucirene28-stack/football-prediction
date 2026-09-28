"""Conservative read-only projection for the six-tab frontend.

Only independently named fields with clear meanings are shown. Malformed source
HTML tables and unconfirmed corner water semantics remain in private snapshots.
"""
from typing import Any

# 新版采集器七模块标识（collector_v2 快照专用展示）
COLLECTOR_V2_MODULES = ("overview", "lineup", "history", "europe", "asia", "ranking", "betfair")

MODULES = {
    "analysis": ("分析", "match_base"),
    "asia": ("亚让", "asia_stats"),
    "total_goals": ("总进球", "total_goals"),
    "wdl": ("胜平负", "europe_stats"),
    "corners": ("角球", "corners"),
    "live_analysis": ("现场分析", "live_analysis"),
}


def _valid_dict(v: Any) -> bool:
    return isinstance(v, dict) and bool(v)


def _titan_module(key: str, v: Any):
    if not _valid_dict(v):
        return None
    if key == "analysis":
        notes = v.get("说明文字")
        briefing = notes.get("赛前简报") if isinstance(notes, dict) else None
        if isinstance(briefing, str) and briefing.strip():
            # The text is a source-provided briefing, not a verified assessment.
            text = briefing.split("未来五场", 1)[0].strip()
            text = text.removeprefix("赛前简报").strip()
            if text:
                return {"来源赛前简报（未经独立核实）": text[:1200]}
        return {"status": "pending_review", "reason": "分析表格尚未可靠解析"}
    if key == "asia":
        companies = v.get("公司盘口")
        if not isinstance(companies, list):
            return {"status": "pending_review", "reason": "亚洲让球盘口未解析为标准行"}
        rows = []
        for r in companies:
            if not isinstance(r, dict) or not r.get("公司"):
                continue
            opening = r.get("初盘盘口")
            live = r.get("即时盘口")
            if opening is None and live is None:
                continue
            rows.append(r)
        if not rows:
            return None
        result = {"已抓取盘口公司数": str(len(rows))}
        # Display only a small, labeled, read-only sample, not an invented consensus.
        for i, r in enumerate(rows[:8], 1):
            result[f"公司盘口 {i}（{str(r['公司'])[:50]}）"] = (
                f"初盘 {r.get('初盘盘口') or '—'} | 主水 {r.get('初盘主水') or '—'} | 客水 {r.get('初盘客水') or '—'}；"
                f"即时 {r.get('即时盘口') or '—'} | 主水 {r.get('即时主水') or '—'} | 客水 {r.get('即时客水') or '—'}"
            )
        return result
    if key == "corners":
        # Never present home/away corner water as a confirmed over/under market.
        if isinstance(v.get("公司盘口"), list) and v["公司盘口"]:
            return {"status": "pending_review", "reason": "已保存角球原始盘口，水位方向和盘口语义尚未核实；不入正式角球赔率表"}
        return None
    if key == "wdl":
        if not isinstance(v.get("公司指数"), list) or not v["公司指数"]:
            return None
        return {"status": "pending_review", "reason": "来源胜平负指数尚未完成结构化核实"}
    if key == "live_analysis":
        return {"status": "pending_review", "reason": "现场分析原始表格尚未验证，不能据此认定首发阵容或比赛统计已采集"}
    return None


def _xiaodianhuo_module(key: str, v: Any):
    if not _valid_dict(v):
        return None
    if v.get("status") in ("failed", "error"):
        return {"status": "failed", "reason": str(v.get("reason") or v.get("error") or "unknown")[:160]}
    if v.get("status") in ("missing", "not_collected"):
        return {"status": "not_collected"}
    if v.get("status") == "success":
        # A success flag without valid business fields is NOT usable module data.
        return {"status": "pending_review", "reason": "采集器报告成功，业务字段尚未完成核验"}
    return {"status": "pending_review", "reason": "模块结构尚未核实"}


def _collector_v2_module(v: Any):
    """新版采集器单模块的保守展示：只反映真实采集与有效性，不编造业务字段。"""
    if not isinstance(v, list) or not v:
        return {"status": "not_collected"}
    valid = [i for i in v if isinstance(i, dict) and i.get("valid")]
    if not valid:
        return {"status": "fetch_failed", "reason": "该模块全部响应无效"}
    return {
        "status": "collected",
        "valid_responses": len(valid),
        "total_responses": len(v),
        "note": "业务字段级核验未完成；原始响应保留于快照，不作为预测依据",
    }


def collector_v2_modules(raw: Any) -> dict | None:
    """新版采集器快照 → 七模块真实状态视图；非新版快照返回 None。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("modules"), dict):
        return None
    if not str(raw.get("matchId") or "").strip():
        return None
    mods = raw["modules"]
    missing = set(raw.get("missingModules") or [])
    out = {}
    for name in COLLECTOR_V2_MODULES:
        if name in missing:
            out[name] = {"status": "not_collected"}
            continue
        out[name] = _collector_v2_module(mods.get(name))
    result = {
        "collector": "collector_v2",
        "round_started_at": raw.get("startedAt"),
        "round_finished_at": raw.get("finishedAt"),
        "modules": out,
    }
    if isinstance(raw.get("unclassifiedOdds"), list) and raw["unclassifiedOdds"]:
        result["unclassified_odds_note"] = (
            "存在按公司区分的盘口列表，语义未核实，仅存原始快照，不写入赔率表"
        )
    return result


def project_modules(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
    is_titan = isinstance(raw.get("比赛信息"), dict)
    out = {}
    for key, (titan_key, xdh_key) in MODULES.items():
        out[key] = (_titan_module(key, raw.get(titan_key)) if is_titan
                    else _xiaodianhuo_module(key, details.get(xdh_key)))
    return out
