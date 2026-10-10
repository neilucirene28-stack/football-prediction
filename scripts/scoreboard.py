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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.backtest import calibration_by_class
from engine.jingcai_evaluation import select_earliest
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
    probs = pred.get("p_final_full") or [pred["p_home"], pred["p_draw"], pred["p_away"]]
    brier, logloss = _brier_logloss(probs, outcome)
    m = {
        "outcome": outcome,
        "brier": brier,
        "logloss": logloss,
        "direction_hit": int(max(range(3), key=lambda i: probs[i]) == outcome),
    }
    m["draw_brier"] = (probs[1] - int(outcome == 1)) ** 2
    m["draw_probability"] = probs[1]
    m["draw_actual"] = int(outcome == 1)
    m["wdl_probs"] = list(probs)
    # 每个信号各自的损失（Hedge 学习输入）
    for name, sp in (pred.get("signals") or {}).items():
        if isinstance(sp, (list, tuple)) and len(sp) == 3:
            b, ll = _brier_logloss(sp, outcome)
            m[f"brier_{name}"] = round(b, 4)
            m[f"logloss_{name}"] = round(ll, 4)
    # 让球胜平负
    h = (pred.get("derivatives") or {}).get("handicap_1x2") or {}
    if h.get("line") is not None:
        hp = [h.get(k+"_full", h[k]) for k in ("p_home", "p_draw", "p_away")]
        diff = (hg - ag) + int(h["line"])
        h_out = 0 if diff > 0 else (1 if diff == 0 else 2)
        hb, hll = _brier_logloss(hp, h_out)
        m["handicap_hit"] = int(max(range(3), key=lambda i: hp[i]) == h_out)
        m["handicap_brier"] = round(hb, 4)
        m["handicap_logloss"] = round(hll, 4)
    # Missing archived scores are unknown, not misses; each metric has its own denominator.
    scores = (pred.get("derivatives") or {}).get("top_scores") or []
    for n in (1, 3, 5):
        if len(scores) >= n:
            m[f"top{n}_hit"] = int(f"{hg}-{ag}" in [t.get("score") for t in scores[:n]])
    summary = (pred.get("derivatives") or {}).get("score_summary") or {}
    if "top5_probability" in summary and len(scores) >= 5:
        m["top5_probability"] = summary["top5_probability"]
        m["top5_coverage_error"] = summary["top5_probability"] - m["top5_hit"]
    # 进球误差
    exp_g = (pred.get("derivatives") or {}).get("expected_goals")
    if exp_g is not None:
        m["goals_bias"] = float(exp_g) - (hg + ag)
        m["goals_err"] = round(abs(float(exp_g) - (hg + ag)), 2)
    return m


def aggregate(scored: list[dict]) -> dict:
    n = len(scored)
    if not n:
        return {"n": 0}
    out = {"n": n}
    for key in ["brier", "logloss", "direction_hit", "top1_hit", "top3_hit", "top5_hit",
                "top5_probability", "top5_coverage_error",
                "draw_brier", "draw_probability", "draw_actual", "goals_bias",
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
            """SELECT p.prediction_id,p.match_id,p.model_version,p.league,
                      p.kickoff_at,p.predicted_at,p.p_home,p.p_draw,p.p_away,
                      p.signals,p.derivatives,p.payload,
                      s.home_goals,s.away_goals,s.settled_at
               FROM predictions p
               LEFT JOIN settlements s ON s.prediction_id=p.prediction_id
               WHERE p.kickoff_at > now()-(%s || ' days')::interval
               ORDER BY p.kickoff_at,p.predicted_at,p.prediction_id""",(str(days),))
        return cur.fetchall()


def build_scoreboard(rows, days=30):
    selected,audit=select_earliest(rows)
    scored=[]
    for r in selected:
        if r.get("home_goals") is None:continue
        pred={"p_home":r["p_home"],"p_draw":r["p_draw"],"p_away":r["p_away"],
              "p_final_full":r["payload"]["result"]["p_final_full"],
              "signals":r.get("signals") or {},"derivatives":r.get("derivatives") or {}}
        s=score_one(pred,r);s.update(league=r.get("league") or "未知",model_version=r["model_version"],kickoff_at=r["kickoff_at"])
        scored.append(s)
    by_version={}
    for version in sorted({r['model_version'] for r in selected}):
        group=[s for s in scored if s['model_version']==version]
        by_version[version]={"overall":aggregate(group),"shadow_weights":shadow_weights(group),
            "calibration":calibration_by_class([(tuple(s['wdl_probs']),s['outcome']) for s in group]),
            "by_league":{lg:aggregate([s for s in group if s['league']==lg]) for lg in sorted({s['league'] for s in group})}}
    return {"generated_at":datetime.now(timezone.utc).isoformat(),"days":days,"audit":audit,"by_version":by_version,
            "note":"同场同版本保留最早合格预测；不同版本的评分和影子权重分别统计；来源可用时间未经独立核验。"}


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
    report = build_scoreboard(rows, days)
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
    audit=rep['audit']
    lines=[f"# 竞彩模型评分看板（近 {rep['days']} 天）",f"生成时间：{rep['generated_at']}","",
           f"排除记录：{audit['excluded_rows']}；排除后续重复预测：{audit['later_duplicate_predictions']}；等待最早记录赛果：{audit['pending_selected_predictions']}","",
           "|模型版本|已结算|方向命中|Brier|LogLoss|平局预测/实际|Top5有效场次|",
           "|---|---:|---:|---:|---:|---|---:|"]
    for v,entry in rep['by_version'].items():
        o=entry['overall']
        if not o['n']:
            lines.append(f"|{v}|0|—|—|—|—|0|");continue
        lines.append(f"|{v}|{o['n']}|{o['direction_hit']:.1%}|{o['brier']}|{o['logloss']}|{o['draw_probability']:.1%}/{o['draw_actual']:.1%}|{o.get('top5_hit_n',0)}|")
    lines += ["",rep['note'],"","来源时间未经独立核验，这份看板不等同于正式前瞻验收。影子权重保留在JSON中，未用于生产参数。"]
    return "\n".join(lines)+"\n"


if __name__ == "__main__":
    raise SystemExit(main())
