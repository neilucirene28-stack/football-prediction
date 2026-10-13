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
import os
import re

_PICKS_FILENAME = "tipster-picks.jsonl"
_SHADOW_LOG_FILENAME = "tipster-shadow.jsonl"


def default_paths():
    """推荐号 picks 文件与影子日志的默认位置。

    由 tipster-daily-tracker 每日收集写入 goal workspace。返回 (picks, log)；
    picks 不存在时调用方应视为影子源缺失、保持关闭。
    """
    base = os.path.join(
        os.path.expanduser("~"),
        "workspace/goals/football-prediction-project/hidden_files")
    picks = os.path.join(base, _PICKS_FILENAME)
    log = os.path.join(base, _SHADOW_LOG_FILENAME)
    return picks, log


_SUFFIXES = (" fc", " afc", " cf", " sc", " ud", " ssc", " rcd", " cd")
# 前缀俱乐部词（仅最保守的 afc，如 AFC Bournemouth vs Bournemouth）
_PREFIXES = ("afc ",)
_OU_RE = re.compile(r"^(?:over|under|o|u)\s*(\d+(?:\.\d+)?)$", re.IGNORECASE)


# 中文队名 → 推荐号侧英文拼写（影子匹配用）。
# 预测 JSON 用中文队名，tipster picks 用英文；先翻译再走 normalize_team。
# 只影响影子对比的匹配，不碰任何生产概率与参数。
# 2026-10-13 实测：不加此表时 10 场全是 picks_matched=0（中英对不上），
# 影子评估名存实亡。新增球队按推荐号实际拼写增补。
_CN_ALIASES = {
    "纽卡斯尔喷气机": "Newcastle Jets",
    "大阪钢巴": "Gamba Osaka",
    "上海海港": "Shanghai Port",
    "大田市民": "Daejeon Hana Citizen",
    "布里兰": "Buriram United",
    "北京国安": "Beijing Guoan",
    "朗斯": "Lens",
    "里斯本竞技": "Sporting CP",
    "国际米兰": "Inter",
    "布鲁日": "Club Brugge",
    "阿森纳": "Arsenal",
    "里尔": "Lille OSC",
    "比利亚雷亚尔": "Villarreal",
    "那不勒斯": "Napoli",
    "维京": "Viking",
    "拜仁慕尼黑": "Bayern Munich",
    "莱比锡红牛": "RB Leipzig",
    "PSV埃因霍温": "PSV",
    "加拉塔萨雷": "Galatasaray",
    "巴塞罗那": "Barcelona",
    "马德里竞技": "Atletico Madrid",
    "曼彻斯特联": "Manchester United",
    "斯托克城": "Stoke City",
    "米德尔斯堡": "Middlesbrough",
}


def _to_en(name):
    """中文队名翻成英文；非中文/未知名原样返回。"""
    if not isinstance(name, str):
        return name
    return _CN_ALIASES.get(name.strip(), name)


def normalize_team(name) -> str:
    """本地队名规范化；与 collector 保持同口径但不跨包依赖。"""
    if not isinstance(name, str):
        return ""
    s = name.strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\s+", " ", s)
    for pre in _PREFIXES:
        if s.startswith(pre):
            s = s[len(pre):]
            break
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
    h, a = normalize_team(_to_en(home)), normalize_team(_to_en(away))
    if not h or not a:
        return []
    hits = []
    for d in picks:
        mh = split_match(d.get("match"))
        if not mh:
            continue
        ph, pa = normalize_team(_to_en(mh[0])), normalize_team(_to_en(mh[1]))
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
