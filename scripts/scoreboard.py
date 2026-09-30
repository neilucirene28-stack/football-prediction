#!/usr/bin/env python3
"""评分看板：滚动 Brier / log-loss / 命中率，分联赛、分模型版本、分信号。

只看不调——输出供人读的指标，为 MVP-4 的 Hedge 权重学习准备
per-signal 损失数据。用法：
    python3 scripts/scoreboard.py [--days 30] [--md]   # --md 写周报 md
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.adaptive_weights import shadow_weights  # noqa: E402

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg, dict_row = None, None


def _outcome(hg: int, ag: int) -> int:
    return 0 if hg > ag else (1 if hg == ag else 2)


def _brier_logloss(probs, outcome):
    brier = sum((p - (1.0 if i == outcome else 0.0)) ** 2
                for i, p in enumerate(probs)) / 3.0
    logloss = -math.log(max(probs[outcome], 1e-12))
    return brier, logloss


def score_one(pred: dict, st: dict) -> dict:
    """单场评分。pred: predictions 行（含 JSON 字段已解析），st: settlements 行。"""
    hg, ag = st["home_goals"], st["away_goals"]
    outcome = _outcome(hg, ag)
    probs = [pred["p_home"], pred["p_draw"], pred["p_away"]]
    brier, logloss = _brier_logloss(probs, outcome)
    m = {
        "outcome": outcome,
        "brier": round(brier, 4),
        "logloss": round(logloss, 4),
        "direction_hit": int(max(range(3), key=lambda i: probs[i]) == outcome),
    }
    # 每个信号各自的损失（Hedge 学习输入）
    for name, sp in (pred.get("signals") or {}).items():
        if isinstance(sp, (list, tuple)) and len(sp) == 3:
            b, ll = _brier_logloss(sp, outcome)
            m[f"brier_{name}"] = round(b, 4)
            m[f"logloss_{name}"] = round(ll, 4)
    # 让球胜平负
    h = (pred.get("derivatives") or {}).get("handicap_1x2") or {}
    if h.get("line") is not None:
        hp = [h["p_home"], h["p_draw"], h["p_away"]]
        diff = (hg - ag) - int(h["line"])
        h_out = 0 if diff > 0 else (1 if diff == 0 else 2)
        hb, hll = _brier_logloss(hp, h_out)
        m["handicap_hit"] = int(max(range(3), key=lambda i: hp[i]) == h_out)
        m["handicap_brier"] = round(hb, 4)
        m["handicap_logloss"] = round(hll, 4)
    # 比分 Top3
    top3 = ((pred.get("derivatives") or {}).get("top_scores") or [])[:3]
    m["top3_hit"] = int(f"{hg}-{ag}" in [t.get("score") for t in top3])
    # 进球误差
    exp_g = (pred.get("derivatives") or {}).get("expected_goals")
    if exp_g is not None:
        m["goals_err"] = round(abs(float(exp_g) - (hg + ag)), 2)
    return m


def aggregate(scored: list[dict]) -> dict:
    n = len(scored)
    if not n:
        return {"n": 0}
    out = {"n": n}
    for key in ["brier", "logloss", "direction_hit", "top3_hit",
                "handicap_hit", "handicap_brier", "handicap_logloss",
                "goals_err",
                "brier_model", "logloss_model",
                "brier_market", "logloss_market",
                "brier_elo", "logloss_elo"]:
        vals = [s[key] for s in scored if key in s]
        if vals:
            out[key] = round(sum(vals) / len(vals), 4)
            out[key + "_n"] = len(vals)
    return out


def _load_rows(conn, days: int) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT p.prediction_id, p.model_version, p.league,
                      p.kickoff_at, p.p_home, p.p_draw, p.p_away,
                      p.signals, p.derivatives,
                      s.home_goals, s.away_goals
               FROM predictions p
               JOIN settlements s ON s.prediction_id = p.prediction_id
               WHERE p.kickoff_at > now() - (%s || ' days')::interval
               ORDER BY p.kickoff_at""",
            (str(days),))
        rows = []
        for r in cur.fetchall():
            rows.append({
                "pred": {
                    "p_home": r["p_home"], "p_draw": r["p_draw"],
                    "p_away": r["p_away"],
                    "signals": r["signals"] or {},
                    "derivatives": r["derivatives"] or {},
                },
                "st": {"home_goals": r["home_goals"],
                       "away_goals": r["away_goals"]},
                "league": r["league"] or "未知",
                "model_version": r["model_version"],
                "kickoff_at": r["kickoff_at"],
            })
        return rows


def main() -> int:
    days = 30
    want_md = False
    for a in sys.argv[1:]:
        if a == "--md":
            want_md = True
        elif a.startswith("--days"):
            days = int(a.split("=")[1])
    url = os.environ.get("DATABASE_URL")
    if not url or not psycopg:
        print("no DATABASE_URL or psycopg")
        return 0
    conn = psycopg.connect(url)
    try:
        rows = _load_rows(conn, days)
    finally:
        conn.close()
    scored = []
    for r in rows:
        s = score_one(r["pred"], r["st"])
        s["league"], s["model_version"] = r["league"], r["model_version"]
        s["kickoff_at"] = r["kickoff_at"]
        scored.append(s)
    shadow = shadow_weights(scored)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "overall": aggregate(scored),
        "shadow_weights": shadow,  # 影子模式：只看不切换
        "by_league": {lg: aggregate([s for s in scored if s["league"] == lg])
                      for lg in sorted({s["league"] for s in scored})},
        "by_version": {v: aggregate([s for s in scored if s["model_version"] == v])
                      for v in sorted({s["model_version"] for s in scored})},
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if want_md:
        path = os.path.expanduser(
            "~/workspace/your_files/"
            f"{datetime.now().strftime('%Y-%m-%d')}-模型评分看板.md")
        with open(path, "w") as f:
            f.write(_render_md(report))
        print(f"report -> {path}", flush=True)
    return 0


def _render_md(rep: dict) -> str:
    o = rep["overall"]
    lines = [f"# 模型评分看板（近 {rep['days']} 天）",
             f"生成时间：{rep['generated_at']}", "",
             f"已结算场次：{o.get('n', 0)}", ""]
    if o.get("n"):
        lines += [
            f"- 方向命中：{o.get('direction_hit', 0):.1%}（n={o.get('direction_hit_n', 0)}）",
            f"- Brier：{o.get('brier')} / LogLoss：{o.get('logloss')}",
            f"- 比分 Top3 命中：{o.get('top3_hit', 0):.1%}",
            f"- 让球胜平负命中：{o.get('handicap_hit', 0):.1%}（n={o.get('handicap_hit_n', 0)}）",
            f"- 进球期望平均误差：{o.get('goals_err', '-')} 球", "",
            "## 分信号 Brier（Hedge 学习输入）", ""]
        for sig in ["model", "market", "elo"]:
            b, n = o.get(f"brier_{sig}"), o.get(f"brier_{sig}_n", 0)
            if b is not None:
                lines.append(f"- {sig}：Brier {b}（n={n}）")
        sh = rep.get("shadow_weights") or {}
        lines += ["", "## Hedge 影子权重（只看不切换）", ""]
        if sh.get("weights"):
            w = sh["weights"]
            lines.append(
                f"- 自适应权重：model {w.get('model', 0):.3f} / "
                f"market {w.get('market', 0):.3f} / elo {w.get('elo', 0):.3f} "
                f"（n_eff={sh.get('n_eff')}）")
            lines.append("- 影子模式运行 2–4 周、持续优于静态权重后才考虑切换。")
        else:
            lines.append(
                f"- 样本不足（n_eff={sh.get('n_eff', 0)}<15），权重未激活，"
                "继续用静态权重。")
        lines += ["", "## 分联赛", ""]
        for lg, a in rep["by_league"].items():
            if a.get("n"):
                lines.append(
                    f"- {lg}：n={a['n']}，方向 {a.get('direction_hit', 0):.1%}，"
                    f"Brier {a.get('brier')}")
        lines += ["", "## 分模型版本", ""]
        for v, a in rep["by_version"].items():
            if a.get("n"):
                lines.append(
                    f"- {v}：n={a['n']}，方向 {a.get('direction_hit', 0):.1%}，"
                    f"Brier {a.get('brier')}")
        lines += ["", "> 只看不调：样本 <100 场不碰任何参数。",
                  "> 结构参数（rho/decay/级别修正）永不在线漂移。"]
    else:
        lines.append("暂无已结算样本。")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
