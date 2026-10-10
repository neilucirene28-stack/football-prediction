#!/usr/bin/env python3
"""Walk-forward 回测：对历史已完赛比赛，用赛前快照（as-of 数据）跑模型。

防泄漏规则：
- 球队近况 / H2H 只取比赛日期之前的数据
- 只用初盘赔率（静态，不随时间变）
- predict(asof=开球前1小时)，快照时间设为开球前1小时
输出 /tmp/walkforward_results.json
"""
import sys, json, time, re
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from collector.collector.sources.titan007 import Titan007Source, _X12JS_URL, _ANALYSIS_URL
from engine.jingcai_predictor import predict, PredictError

TZ8 = timezone(timedelta(hours=8))

def _parse_dt(s):
    return datetime.fromisoformat(s)

def _get_with_retry(src, url, tries=3):
    import time as _t
    for i in range(tries):
        try:
            return src._get(url)
        except Exception:
            if i == tries - 1:
                raise
            _t.sleep(3)

def build_asof_payload(src, mid, kickoff_s):
    """kickoff_s: '2026-09-20 18:00'。返回 (payload, outcome) 或 (None, reason)。"""
    ko_date = kickoff_s[:10]
    ko_dt = datetime.strptime(kickoff_s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ8)

    # -- x12js: 球队 / 中立场 / 初盘 --
    try:
        x = src.parse_x12js(_get_with_retry(src, _X12JS_URL.format(mid=mid)))
    except Exception as e:
        return None, f"x12js fail {type(e).__name__}"
    # 覆盖度过滤：初盘公司数太少说明数据质量差
    n_comp = len([c for c in (x.get("companies") or []) if c.get("open")])
    if n_comp < 8:
        return None, f"companies too few ({n_comp})"
    home_id, away_id = x.get("hometeamID"), x.get("guestteamID")
    if not home_id or not away_id:
        return None, "no team ids"
    home = x.get("hometeam_cn") or ""
    away = x.get("guestteam_cn") or ""
    neutral = x.get("neutrality") == "1"

    # -- analysis: 带日期的历史 --
    try:
        ana = src.parse_analysis(_get_with_retry(src, _ANALYSIS_URL.format(mid=mid)),
                                 int(home_id), int(away_id))
    except Exception as e:
        return None, f"analysis fail {type(e).__name__}"
    h_hist = ana.get("home_recent", []) or []
    a_hist = ana.get("away_recent", []) or []
    h2h_all = ana.get("h2h", []) or []

    # -- 实际比分：先主队历史，找不到找客队历史（视角反转） --
    outcome = actual = None
    for r in h_hist:
        if r.get("date") == ko_date:
            gf, ga = r["gf"], r["ga"]
            actual, outcome = (gf, ga), (0 if gf > ga else (1 if gf == ga else 2))
            break
    if outcome is None:
        for r in a_hist:
            if r.get("date") == ko_date:
                gf, ga = r["gf"], r["ga"]  # 客队视角：gf=客队进球
                actual, outcome = (ga, gf), (0 if ga > gf else (1 if ga == gf else 2))
                break
    if outcome is None:
        return None, "score not found in history"

    # -- as-of 近况：严格早于比赛日 --
    h_asof = [r for r in h_hist if r.get("date", "") < ko_date][:10]
    a_asof = [r for r in a_hist if r.get("date", "") < ko_date][:10]
    h2h_asof = [r for r in h2h_all if r.get("date", "") < ko_date][:6]

    payload = {
        "home": home, "away": away,
        "home_team": home, "away_team": away,
        "kickoff_at": ko_dt.isoformat(),
        "snapshot_at": (ko_dt - timedelta(hours=1)).isoformat(),
        "home_recent": h_asof, "away_recent": a_asof, "h2h": h2h_asof,
        "neutral_site": neutral,
        "competition": x.get("matchname_cn", ""),
    }
    # -- 初盘赔率（静态，无泄漏）：x12js 自带 companies[].open --
    avg_open = src._avg_x12(x.get("companies") or [], "open")
    if avg_open:
        payload["odds"] = avg_open
        payload["opening_odds"] = avg_open
    return payload, {"outcome": outcome, "score": actual,
                     "n_home_form": len(h_asof), "n_away_form": len(a_asof)}

def main():
    matches = json.load(open("/tmp/finished_matches.json"))
    print(f"matches: {len(matches)}", flush=True)
    src = Titan007Source(request_delay=0.6)
    results = []
    for i, m in enumerate(matches):
        mid, ko = m["matchid"], m["kickoff"]
        try:
            payload, meta = build_asof_payload(src, mid, ko)
            if payload is None:
                print(f"[{i}] {mid} skip: {meta}", flush=True); continue
            ko_dt = datetime.strptime(ko, "%Y-%m-%d %H:%M").replace(tzinfo=TZ8)
            asof = ko_dt - timedelta(hours=1)
            res = predict(payload, None, asof=asof)
            if res.get("status") == "insufficient_data":
                print(f"[{i}] {mid} insufficient", flush=True); continue
            # 市场基线：初盘隐含概率
            o = payload.get("odds")
            mkt = None
            if o:
                inv = [1/o["home"], 1/o["draw"], 1/o["away"]]
                s = sum(inv)
                mkt = [v/s for v in inv]
            results.append({
                "matchid": mid, "kickoff": ko,
                "home": payload["home"], "away": payload["away"],
                "model": [res["p_home"], res["p_draw"], res["p_away"]],
                "market": mkt,
                "outcome": meta["outcome"], "score": meta["score"],
                "grade": res["grade"],
                "n_home_form": meta["n_home_form"],
                "n_away_form": meta["n_away_form"],
            })
            print(f"[{i}] {ko[:10]} {payload['home']} {meta['score']} {payload['away']} "
                  f"outcome={meta['outcome']} model={[round(v,2) for v in [res['p_home'],res['p_draw'],res['p_away']]]}",
                  flush=True)
        except PredictError as e:
            print(f"[{i}] {mid} PredictError: {e}", flush=True)
        except Exception as e:
            print(f"[{i}] {mid} ERROR {type(e).__name__}: {e}", flush=True)
        time.sleep(0.4)
    json.dump(results, open("/tmp/walkforward_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"DONE: {len(results)} / {len(matches)}", flush=True)

main()
