"""外部推荐号影子对比（竞彩 v2）。

用途：把 eaglepredict 等 Tier A 推荐号的公开预测与引擎输出做一致性对比，
只记录一致/分歧，**永不写入任何生产参数**。

启用方式（predict() 的 config）：
    {"tipster_shadow": True,
     "tipster_picks_path": "/path/to/tipster-picks.jsonl",
     "tipster_shadow_tipster": "eaglepredict",   # 可选，默认 eaglepredict
     "tipster_shadow_log": "/path/to/shadow.jsonl"}  # 可选，追加影子日志

默认关闭：config 里没有 tipster_shadow=True 时 predict() 行为零变化。
本模块内部捕获所有异常，attach_shadow 永不向上传播。
"""

from __future__ import annotations

import json
import re

_SUFFIXES = (" fc", " afc", " cf", " sc")
_OU_RE = re.compile(r"^(?:over|under|o|u)\s*(\d+(?:\.\d+)?)$", re.IGNORECASE)


def normalize_team(name) -> str:
    """本地队名规范化；与 collector 保持同口径但不跨包依赖。"""
    if not isinstance(name, str):
        return ""
    s = name.strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\s+", " ", s)
    for suf in _SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s.strip()


def split_match(match) -> tuple[str, str] | None:
    if not isinstance(match, str):
        return None
    parts = re.split(r"\s+vs\s+", match.strip(), flags=re.IGNORECASE)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def load_picks(path, tipster="eaglepredict") -> list[dict]:
    """从 tipster-picks.jsonl 读出指定推荐号的全部记录。"""
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("tipster") == tipster:
                out.append(d)
    return out


def find_matches(home, away, picks) -> list[dict]:
    """按主客队规范化匹配；主客不分也可命中（返回时标注是否反位）。"""
    h, a = normalize_team(home), normalize_team(away)
    if not h or not a:
        return []
    hits = []
    for d in picks:
        mh = split_match(d.get("match"))
        if not mh:
            continue
        ph, pa = normalize_team(mh[0]), normalize_team(mh[1])
        if ph == h and pa == a:
            hits.append((d, False))
        elif ph == a and pa == h:
            hits.append((d, True))
    return hits


def _parse_1x2_set(pick) -> set[str] | None:
    p = str(pick).strip().upper().replace(" ", "")
    mapping = {
        "1": {"1"},
        "X": {"X"},
        "2": {"2"},
        "1X": {"1", "X"},
        "X1": {"1", "X"},
        "12": {"1", "2"},
        "21": {"1", "2"},
        "X2": {"X", "2"},
        "2X": {"X", "2"},
    }
    return mapping.get(p)


def _parse_ou(pick) -> tuple[str, float] | None:
    """解析 O1.5 / Over 1.5 / U3.5 / Under 3.5 → ('over'|'under', line)。"""
    p = str(pick).strip().replace(" ", "")
    m = _OU_RE.match(p)
    if not m:
        return None
    pl = p.lower()
    if pl.startswith("over") or pl.startswith("o"):
        side = "over"
    elif pl.startswith("under") or pl.startswith("u"):
        side = "under"
    else:
        return None
    return side, float(m.group(1))


def _parse_btts(pick) -> bool | None:
    p = str(pick).strip().upper().replace("-", "").replace(" ", "")
    if p in ("BTTSY", "YES", "BTTS", "GG"):
        return True
    if p in ("BTTSN", "NO"):
        return False
    return None


def compare_one(market, pick, out) -> dict:
    """单条推荐与引擎输出的对比。out 是 predict() 的结果字典。"""
    deriv = (out.get("derivatives") or {})
    rec = (out.get("recommendation") or {})
    direction = rec.get("direction")
    engine_1x2 = {"主胜": "1", "平局": "X", "客胜": "2"}.get(direction)

    mkt = str(market or "").strip().upper()
    record = {"market": market, "pick": pick, "verdict": "skipped", "note": ""}

    if mkt == "1X2":
        pick_set = _parse_1x2_set(pick)
        record["engine_direction"] = direction
        record["engine_pick"] = engine_1x2
        if pick_set is None or engine_1x2 is None:
            record["note"] = "无法解析推荐或引擎方向"
        elif len(pick_set) == 1:
            record["verdict"] = "agree" if engine_1x2 in pick_set else "disagree"
        else:
            record["verdict"] = "partial" if engine_1x2 in pick_set else "disagree"
            record["note"] = "双选推荐，引擎首选在其覆盖范围内" if record["verdict"] == "partial" else ""
    elif mkt == "OU":
        parsed = _parse_ou(pick)
        if parsed is None:
            record["note"] = "无法解析大小球盘口"
        else:
            side, line = parsed
            ou = deriv.get("over_under") or {}
            engine_over = engine_under = None
            source = ""
            if ou.get("line") is not None and abs(float(ou["line"]) - line) < 1e-9:
                engine_over, engine_under = ou.get("over"), ou.get("under")
                source = f"引擎同盘口(line={line})"
            else:
                exp_goals = deriv.get("expected_goals_full")
                if exp_goals is not None:
                    gap = float(exp_goals) - line
                    engine_over = gap > 0.3
                    engine_under = gap < -0.3
                    source = f"期望总进球 {float(exp_goals):.2f} vs 盘口 {line}"
            record["engine_evidence"] = source
            if engine_over is None:
                record["verdict"] = "neutral"
                record["note"] = "引擎无可比大小球信号"
            elif side == "over":
                record["verdict"] = "agree" if (engine_over if isinstance(engine_over, bool) else engine_over > 0.5) else "disagree"
            else:
                record["verdict"] = "agree" if (engine_under if isinstance(engine_under, bool) else engine_under > 0.5) else "disagree"
    elif mkt == "BTTS":
        want_yes = _parse_btts(pick)
        btts = deriv.get("btts_full")
        record["engine_btts"] = btts
        if want_yes is None or btts is None:
            record["verdict"] = "neutral"
            record["note"] = "无法解析推荐或引擎无BTTS信号"
        else:
            engine_yes = btts > 0.5
            record["verdict"] = "agree" if engine_yes == want_yes else "disagree"
    else:
        record["note"] = f"暂不支持该玩法({market})，如ACCA多串"

    # 推荐赔率原样带回，便于影子期结算
    return record


def compare_all(out, picks) -> dict:
    """对一批匹配到的推荐逐条对比，返回汇总报告。"""
    comparisons = []
    summary = {"agree": 0, "partial": 0, "disagree": 0, "neutral": 0, "skipped": 0}
    for d, swapped in picks:
        rec = compare_one(d.get("market"), d.get("pick"), out)
        rec["odds"] = d.get("odds")
        rec["posted_at"] = d.get("posted_at")
        if swapped:
            rec["note"] = (rec.get("note") or "") + "（主客队反位命中）"
        comparisons.append(rec)
        summary[rec["verdict"]] = summary.get(rec["verdict"], 0) + 1
    return {
        "status": "ok",
        "picks_matched": len(picks),
        "comparisons": comparisons,
        "summary": summary,
    }


def attach_shadow(out, picks_path, home, away, tipster="eaglepredict",
                  log_path=None) -> dict:
    """把影子对比挂到 predict() 输出上；内部永不抛异常。"""
    try:
        picks = load_picks(picks_path, tipster=tipster)
        hits = find_matches(home, away, picks)
        report = compare_all(out, hits)
        report["tipster"] = tipster
        out["tipster_shadow"] = report
        if log_path and report["picks_matched"]:
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "generated_at": out.get("prediction_generated_at"),
                        "match": {"home": home, "away": away},
                        "tipster": tipster,
                        "summary": report["summary"],
                        "comparisons": report["comparisons"],
                    }, ensure_ascii=False) + "\n")
            except OSError:
                pass
    except Exception as e:  # noqa: BLE001 - 影子永不干扰主流程
        out["tipster_shadow"] = {"status": "skipped", "reason": str(e)[:200]}
    return out
