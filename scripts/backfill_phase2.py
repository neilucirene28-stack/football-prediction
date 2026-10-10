#!/usr/bin/env python3
"""Phase 2 回填：226场非FD覆盖赛事，澳客 history 战绩，严格 as-of，走生产 v2.4 predict()。

- 清单：data/phase2_matches.json（竞彩赔率/让球/开球时间）
- 战绩：data/phase2_history.json（每队 as-of 前 form，venue 已校验）
- 无 Elo（跨联赛无共享历史，诚实缺失）、无 H2H（未采集）
- league_avg_goals：两队 form 总进球均值（as-of 数据）
- 市场：竞彩固定赔率（无初盘，无 drift）
- asof = 开球前1小时；同日不批量更新（无 Elo）
"""
import json, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

TZ = timezone(timedelta(hours=8))
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from engine.jingcai_predictor import predict, PredictError

BASE = "/home/hatch/workspace/football-prediction-v2"
HIST = f"{BASE}/data/phase2_history.json"
MLIST = f"{BASE}/data/phase2_matches.json"
OUT = f"{BASE}/hidden_backfill_phase2.jsonl"


def to_recent(entries, team_side):
    """澳客 form -> predictor home_recent 格式。team_side: 'home' 或 'away' 指被统计球队在目标比赛中的身份。

    venue 编码约定与 engine/strengths.attack_defense 一致：'H'/'A'/'N'。
    （2026-10-07 修正：此前输出小写 "home"/"away"，被 attack_defense 的
    venue='H'/'A' 过滤条件静默丢弃，导致回填近况全部失效。）
    """
    out = []
    for e in entries:
        is_home = e["venue"] == "home"
        gf = e["hg"] if is_home else e["ag"]
        ga = e["ag"] if is_home else e["hg"]
        if gf is None or ga is None:
            continue
        opp = e["away"] if is_home else e["home"]
        res = "W" if gf > ga else ("D" if gf == ga else "L")
        out.append({"opponent": opp, "home_away": "H" if is_home else "A",
                    "gf": int(gf), "ga": int(ga), "result": res,
                    "date": e["date"], "venue": "H" if is_home else "A"})
    return out


def league_avg(entries_h, entries_a):
    gs = []
    for e in entries_h + entries_a:
        if e["hg"] is not None and e["ag"] is not None:
            gs.append(e["hg"] + e["ag"])
    return round(sum(gs) / len(gs), 3) if gs else 2.7


def main():
    hist = {m["mido"]: m for m in json.load(open(HIST))}
    mlist = json.load(open(MLIST))
    print(f"清单 {len(mlist)} 场，战绩 {len(hist)} 场", flush=True)

    results, skipped = [], []
    for j in mlist:
        mid = j["mido"]
        h = hist.get(mid)
        if not h or "error" in h:
            skipped.append((mid, "no_history")); continue
        hr_raw = h.get("home_form") or []
        ar_raw = h.get("away_form") or []
        hr = to_recent(hr_raw, "home")
        ar = to_recent(ar_raw, "away")
        ko = datetime.strptime(j["kickoff"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        asof = ko - timedelta(hours=1)
        # 防泄漏：form 日期必须 < 开球日期
        dstr = j["kickoff"][:10]
        if any(e["date"] >= dstr for e in hr_raw + ar_raw):
            skipped.append((mid, "asof_leak")); continue
        odds = j.get("odds")
        if odds and any(x in (0, None) for x in odds):
            odds = None  # 竞彩未开赔率：无市场信号
        payload = {
            "home": j["home"], "away": j["away"],
            "league": j.get("league", ""),
            "kickoff_at": ko.isoformat(),
            "snapshot_at": (ko - timedelta(hours=3)).isoformat(),
            "home_recent": hr, "away_recent": ar,
            "h2h": [],
            "league_avg_goals": league_avg(hr_raw, ar_raw),
            "odds": {"home": odds[0], "draw": odds[1], "away": odds[2]} if odds else None,
            "ou_line": 2.5,
        }
        try:
            res = predict(payload, None, asof=asof)
        except PredictError as e:
            skipped.append((mid, f"predict:{e}")); continue
        if res.get("status") == "insufficient_data":
            skipped.append((mid, "insufficient_data")); continue
        actual = None
        if h.get("hg") is not None and h.get("ag") is not None:
            actual = "H" if h["hg"] > h["ag"] else ("D" if h["hg"] == h["ag"] else "A")
        rec = {
            "mido": mid, "date": dstr, "league": j.get("league"),
            "home": j["home"], "away": j["away"],
            "actual": actual, "score": f"{h.get('hg')}-{h.get('ag')}",
            "p": [res["p_home"], res["p_draw"], res["p_away"]],
            "signals": res.get("signals"), "weights": res.get("weights"),
            "divergence": res.get("divergence"), "grade": res.get("grade"),
            "completeness": res.get("completeness"),
            "top_score": (res.get("derivatives", {}) or {}).get("top_scores", [{}])[0],
            "expected_goals": (res.get("derivatives", {}) or {}).get("expected_goals"),
            "ou_pred": (res.get("derivatives", {}) or {}).get("over_under"),
            "lam": [res.get("lambda_home"), res.get("lambda_away")],
            "jc_odds": odds, "jc_rq": j.get("rq"),
            "n_form": [len(hr), len(ar)],
        }
        results.append(rec)
        if len(results) % 50 == 0:
            print(f"  已预测 {len(results)}", flush=True)

    with open(OUT, "w") as fh:
        for rec in results:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"完成 {len(results)} 场，跳过 {len(skipped)} 场 → {OUT}", flush=True)
    for s in skipped:
        print("  skip:", s, flush=True)


if __name__ == "__main__":
    main()
