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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg, dict_row = None, None


def _outcome(hg: int, ag: int) -> int:
    return 0 if hg > ag else (1 if hg == ag else 2)


def _brier_logloss(probs, outcome):
    brier = sum((p - (1.0 if i == outcome else 0.0)) ** 2
                for i, p in enumerate(probs))
    logloss = -math.log(max(probs[outcome], 1e-12))
    return brier, logloss


def checked_distribution(values, size=3):
    if not isinstance(values, (list, tuple)) or len(values) != size:
        raise ValueError(f"概率须有{size}项")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
        raise ValueError("概率必须有限且在0～1之间")
    mass = sum(values)
    if abs(mass - 1) > max(.00031, size*.000051):
        raise ValueError("概率之和不为1")
    return tuple(v / mass for v in values)


def checked_probabilities(values):
    return checked_distribution(values)


def score_one(pred: dict, st: dict) -> dict:
    """单场评分。pred: predictions 行（含 JSON 字段已解析），st: settlements 行。"""
    hg, ag = st["home_goals"], st["away_goals"]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (hg, ag)):
        raise ValueError("最终比分必须为非负整数")
    outcome = _outcome(hg, ag)
    probs = checked_probabilities(pred["p_final_full"] if pred.get("p_final_full") is not None
                                  else [pred["p_home"], pred["p_draw"], pred["p_away"]])
    brier, logloss = _brier_logloss(probs, outcome)
    m = {
        "outcome": outcome,
        "brier": round(brier, 4),
        "brier_full": brier, "logloss_full": logloss,
        "brier_class_mean": round(brier / 3.0, 4),
        "metric_schema": "multiclass_brier_sum-v2",
        "logloss": round(logloss, 4),
        "direction_hit": int(max(range(3), key=lambda i: probs[i]) == outcome),
        "draw_brier": (probs[1] - int(outcome == 1))**2,
    }
    # 每个信号各自的损失（Hedge 学习输入）
    m["invalid_signals"] = []
    for name, sp in (pred.get("signals_full") or pred.get("signals") or {}).items():
        if isinstance(sp, (list, tuple)) and len(sp) == 3:
            try:
                sp = checked_probabilities(sp)
            except ValueError:
                m["invalid_signals"].append(name)
                continue
            b, ll = _brier_logloss(sp, outcome)
            m[f"brier_{name}"] = round(b, 4)
            m[f"logloss_{name}"] = round(ll, 4)
            m[f"brier_{name}_full"] = b
            m[f"logloss_{name}_full"] = ll
        elif sp is not None:
            m["invalid_signals"].append(name)
    # 让球胜平负
    h = (pred.get("derivatives") or {}).get("handicap_1x2") or {}
    if h.get("line") is not None:
        from engine.jingcai_handicap import integer_handicap
        hp = checked_probabilities([h[f"p_{k}_full"] if f"p_{k}_full" in h else h[f"p_{k}"]
                                    for k in ("home", "draw", "away")])
        diff = (hg - ag) + integer_handicap(h["line"])
        h_out = 0 if diff > 0 else (1 if diff == 0 else 2)
        hb, hll = _brier_logloss(hp, h_out)
        m["handicap_hit"] = int(max(range(3), key=lambda i: hp[i]) == h_out)
        m["handicap_brier"] = round(hb, 4)
        m["handicap_brier_full"] = hb
        m["handicap_logloss_full"] = hll
        m["handicap_brier_class_mean"] = round(hb / 3.0, 4)
        m["handicap_outcome"] = h_out
        m["handicap_logloss"] = round(hll, 4)
        m["handicap_draw_brier"] = (hp[1] - int(h_out == 1))**2
    # 比分 Top3
    deriv = pred.get("derivatives") or {}
    scores = deriv.get("top_scores") or []
    if scores:
        m["top3_hit"] = int(f"{hg}-{ag}" in [t.get("score") for t in scores[:3]])
        m["top5_hit"] = int(f"{hg}-{ag}" in [t.get("score") for t in scores[:5]])
    # 进球误差
    exp_g = deriv.get("expected_goals_full", deriv.get("expected_goals"))
    if exp_g is not None:
        if not math.isfinite(float(exp_g)) or float(exp_g) < 0:
            raise ValueError("期望进球数无效")
        m["goals_err"] = round(abs(float(exp_g) - (hg + ag)), 2)
        m["goals_err_full"] = abs(float(exp_g) - (hg + ag))
        m["goals_bias"] = float(exp_g) - (hg + ag)
    m["unscored_playtypes"] = []
    def score_distribution(name, mapping, labels, actual):
        if not mapping:
            m["unscored_playtypes"].append(name + ":missing_probabilities")
            return
        try:
            values = checked_distribution([mapping[label] for label in labels], len(labels))
        except (ValueError, KeyError, TypeError):
            m["unscored_playtypes"].append(name + ":invalid_probabilities")
            return
        b, ll = _brier_logloss(values, labels.index(actual))
        m[name + "_brier"] = round(b, 4); m[name + "_brier_full"] = b
        m[name + "_logloss"] = round(ll, 4); m[name + "_logloss_full"] = ll
        m[name + "_hit"] = int(labels[max(range(len(labels)), key=values.__getitem__)] == actual)
    total_labels = [str(k) for k in range(7)] + ["7+"]
    score_distribution("total_goals", deriv.get("total_goals_exact_full") or deriv.get("total_goals_exact"),
                       total_labels, str(hg+ag) if hg+ag < 7 else "7+")
    btts = deriv.get("btts_full", deriv.get("btts"))
    if btts is not None:
        if isinstance(btts, bool) or not isinstance(btts, (int, float)) or not math.isfinite(btts) or not 0 <= btts <= 1:
            m["unscored_playtypes"].append("btts:invalid_probability")
        else:
            m["btts_brier"] = (btts-int(hg > 0 and ag > 0))**2
    ht_h, ht_a = st.get("ht_home"), st.get("ht_away")
    if ht_h is None or ht_a is None:
        m["unscored_playtypes"].extend(["half_time:missing_result", "half_full:missing_result"])
    elif any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in (ht_h, ht_a)) or ht_h > hg or ht_a > ag:
        m["unscored_playtypes"].extend(["half_time:invalid_result", "half_full:invalid_result"])
    else:
        ht_out = _outcome(ht_h, ht_a)
        ht = deriv.get("half_time") or {}
        ht_mapping = deriv.get("half_time_full") or (
            {name: ht.get(f"p_{name}") for name in ("home", "draw", "away")} if ht else None)
        score_distribution("half_time", ht_mapping, ["home", "draw", "away"], ["home", "draw", "away"][ht_out])
        labels = [a+b for a in ("胜", "平", "负") for b in ("胜", "平", "负")]
        score_distribution("half_full", deriv.get("half_full_1x2_full") or deriv.get("half_full_1x2"),
                           labels, ("胜", "平", "负")[ht_out]+("胜", "平", "负")[outcome])
    from engine.jingcai_cards import count
    cards=deriv.get("cards") or {}
    if not isinstance(cards,dict):
        m["unscored_playtypes"].append("cards:invalid_prediction");cards={}
    observed={}
    for key in ("yellow_home","yellow_away","red_home","red_away"):
        value=st.get(key)
        if value is None:continue
        try:observed[key]=count(value,key)
        except ValueError:m["unscored_playtypes"].append("cards:"+key+":invalid_result")
    def binary_card(name,p,actual):
        if isinstance(p,bool) or not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1:
            m["unscored_playtypes"].append(name+":invalid_probability");return
        m[name+"_brier"]=(p-actual)**2
        m[name+"_logloss"]=-math.log(max(p if actual else 1-p,1e-12))
    red_probs=cards.get("red_probabilities_full") or {}
    if not isinstance(red_probs,dict):red_probs={}
    yellow_probs=cards.get("yellow_over_probabilities_full") or {}
    if not isinstance(yellow_probs,dict):yellow_probs={}
    for side in ("home","away"):
        key="red_"+side
        if key in observed and cards.get("status")=="ok":
            binary_card(key,red_probs.get(side,cards.get("p_"+side+"_red")),int(observed[key]>0))
    if all("red_"+side in observed for side in ("home","away")) and cards.get("status")=="ok":
        binary_card("red_any",red_probs.get("any",cards.get("p_red")),int(observed["red_home"]+observed["red_away"]>0))
    elif cards.get("status")=="ok":m["unscored_playtypes"].append("red_any:missing_result")
    if all("yellow_"+side in observed for side in ("home","away")) and cards.get("status")=="ok":
        yellow=observed["yellow_home"]+observed["yellow_away"]
        for line in (3.5,4.5):
            binary_card("yellow_over_"+str(line).replace('.','_'),
                yellow_probs.get(str(line),cards.get("p_over_"+str(line).replace('.','_'))),
                int(yellow>line))
        expected_map=cards.get("expected_yellow_full") or {}
        if not isinstance(expected_map,dict):expected_map={}
        expected=expected_map.get("total",cards.get("exp_total_yellow"))
        if isinstance(expected,(int,float)) and not isinstance(expected,bool) and math.isfinite(expected) and expected>=0:
            m["yellow_total_err"]=abs(expected-yellow);m["yellow_total_bias"]=expected-yellow
    elif cards.get("status")=="ok":m["unscored_playtypes"].append("yellow_total:missing_result")
    return m


def aggregate(scored: list[dict]) -> dict:
    n = len(scored)
    if not n:
        return {"n": 0}
    out = {"n": n}
    out["metric_schema"] = "multiclass_brier_sum-v2"
    for key in ["brier", "brier_class_mean", "logloss", "direction_hit", "top3_hit", "top5_hit", "draw_brier",
                "handicap_hit", "handicap_brier", "handicap_logloss",
                "handicap_brier_class_mean",
                "goals_err", "goals_bias", "handicap_draw_brier", "btts_brier",
                "total_goals_brier", "total_goals_logloss", "total_goals_hit",
                "half_time_brier", "half_time_logloss", "half_time_hit",
                "half_full_brier", "half_full_logloss", "half_full_hit",
                "brier_model", "logloss_model",
                "brier_market", "logloss_market",
                "brier_elo", "logloss_elo",
                "red_home_brier","red_home_logloss","red_away_brier","red_away_logloss",
                "red_any_brier","red_any_logloss","yellow_over_3_5_brier","yellow_over_3_5_logloss",
                "yellow_over_4_5_brier","yellow_over_4_5_logloss","yellow_total_err","yellow_total_bias"]:
        vals = [s[key] for s in scored if key in s]
        if vals:
            full_vals = [s.get(key + "_full", s[key]) for s in scored if key in s]
            out[key] = round(sum(full_vals) / len(full_vals), 4)
            out[key + "_n"] = len(vals)
    from collections import Counter
    out["unscored_playtypes"] = dict(Counter(reason for s in scored for reason in s.get("unscored_playtypes", [])))
    return out


def _load_rows(conn, days: int, asof=None) -> list[dict]:
    from engine.jingcai_review import load_review_rows
    asof = asof if asof is not None else datetime.now(timezone.utc)
    return load_review_rows(conn, days=days, asof=asof)


def main() -> int:
    import argparse
    from engine.jingcai_review import build_review
    parser = argparse.ArgumentParser(description="竞彩账本复盘：按版本与同样本比较，不写参数")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--input", type=Path, help="离线账本JSON数组或JSONL；不接收无证据回填")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--md", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.days <= 3650:
        parser.error("days必须在1～3650之间")
    asof = datetime.now(timezone.utc)
    if args.input:
        text = args.input.read_text()
        rows = json.loads(text) if text.lstrip().startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        url = os.environ.get("DATABASE_URL")
        if not url or not psycopg:
            rows = []
        else:
            with psycopg.connect(url, row_factory=dict_row) as conn:
                rows = _load_rows(conn, args.days, asof)
    report = build_review(rows, asof=asof)
    report["days"] = args.days
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    print(output, end="")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(output)
    if args.md:
        path = ROOT/"audit_output"/f"{asof.date().isoformat()}-jingcai-review.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_md(report))
        print(f"report -> {path}", flush=True)
    return 0


def _render_md(rep: dict) -> str:
    lines = ["# 竞彩账本复盘", f"评估截止：{rep['evaluated_asof']}", "",
             f"独立比赛：{rep['unique_physical_matches']}；比赛×版本记录：{rep['match_version_observations']}",
             "每个比赛与版本取最早合规赛前预测；各版本分别评分、分别计算影子权重。", "",
             "| 模型版本 | 场次 | Brier（三项和） | LogLoss | 方向命中 |", "|---|---:|---:|---:|---:|"]
    for version, item in rep["by_version"].items():
        metrics = item["metrics"]
        lines.append(f"| {version} | {metrics['n']} | {metrics['brier']} | {metrics['logloss']} | {metrics['direction_hit']:.1%} |")
        for cohort, shadow in item["shadow_by_signal_cohort"].items():
            lines.append(f"\n{version} / {cohort}：同样本n={shadow['n']}，n_eff={shadow['n_eff']:.2f}，影子权重={shadow['weights']}。")
    lines += ["", f"排除原因与数量：{rep['excluded']}", "",
              "影子权重在这些损失上计算，不能当作样本外提升；没有写回生产参数。",
              "输入摘要仅绑定声明的输入，不认证外部数据真实可得时间。"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
