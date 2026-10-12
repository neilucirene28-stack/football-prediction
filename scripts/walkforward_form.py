#!/usr/bin/env python3
"""Form-only walk-forward 回测：用球队历史时间线做真正的时序验证。

方法：
1. 从多场比赛的 analysis 页收集各队带日期的历史 (date, opponent, gf, ga, venue)
2. 按球队构建时间线，去重，按日期排序
3. 对每支球队 walk-forward：用 match[i] 之前的比赛做 form，预测 match[i]
4. 只用 form 信号（无赔率），验证 Poisson 引擎本身

无泄漏：预测 match[i] 时只用 date < D_i 的数据。
输出 /tmp/wf_form_results.json
"""
import sys, json, time
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
from datetime import datetime, timedelta, timezone
from collector.collector.sources.titan007 import Titan007Source, _X12JS_URL, _ANALYSIS_URL
from engine.jingcai_predictor import predict, PredictError

TZ8 = timezone(timedelta(hours=8))

def collect_histories(src, candidates, max_matches=60):
    """从候选比赛收集球队历史。返回 {team_key: [(date, opp, gf, ga, venue, comp)]}"""
    teams = {}
    for i, c in enumerate(candidates[:max_matches]):
        mid = c["matchid"]
        try:
            x = src.parse_x12js(src._get(_X12JS_URL.format(mid=mid)))
            hid, aid = x.get("hometeamID"), x.get("guestteamID")
            if not hid or not aid:
                continue
            ana = src.parse_analysis(src._get(_ANALYSIS_URL.format(mid=mid)),
                                     int(hid), int(aid))
            hname = x.get("hometeam_cn", f"H{hid}")
            aname = x.get("guestteam_cn", f"A{aid}")
            for key, name, hist in ((f"H{hid}", hname, ana.get("home_recent", [])),
                                    (f"A{aid}", aname, ana.get("away_recent", []))):
                if key not in teams:
                    teams[key] = {"name": name, "rows": []}
                for r in hist or []:
                    d = r.get("date", "")
                    if len(d) == 10 and d[0] == "2":  # 合法日期
                        teams[key]["rows"].append(
                            (d, r.get("opponent", ""), r["gf"], r["ga"],
                             r.get("venue", ""), r.get("comp", "")))
            print(f"[{i}] {hname} vs {aname}: teams={len(teams)}", flush=True)
        except Exception as e:
            print(f"[{i}] {mid} ERR {type(e).__name__}", flush=True)
        time.sleep(0.3)
    # 去重 + 排序
    for key in teams:
        seen, uniq = set(), []
        for row in sorted(teams[key]["rows"]):
            k = (row[0], row[1])
            if k not in seen:
                seen.add(k)
                uniq.append(row)
        teams[key]["rows"] = uniq
    return teams

def walkforward_team(key, team, name_index, min_hist=5):
    """对单支球队 walk-forward。返回 [(probs, outcome)]。
    name_index: 球队名 -> 时间线，用于查对手 as-of 状态。
    venue 感知：根据历史行的 venue 还原真实主客场。"""
    rows = team["rows"]
    if len(rows) < min_hist + 3:
        return []
    results = []
    name = team["name"]
    for i in range(min_hist, len(rows)):
        d, opp, gf, ga, venue, comp = rows[i]
        # gf/ga 是该队视角。还原主客场：
        # venue H=该队主场，A=该队客场，N=中立
        if venue == "A":
            # 该队是客队：交换主客
            h_name, a_name = opp, name
            h_gf, h_ga = ga, gf  # 主队（对手）视角比分
        else:
            h_name, a_name = name, opp
            h_gf, h_ga = gf, ga
        neutral = (venue == "N")

        def _mk_recent(tl_rows):
            return [
                {"gf": r[2], "ga": r[3], "venue": r[4], "date": r[0],
                 "comp": r[5]}
                for r in tl_rows
            ]

        form_rows = rows[max(0, i-10):i]
        # 主队 form：如果是该队主场，用该队历史；否则用对手历史
        if venue == "A":
            # 主队是对手：查对手时间线
            opp_tl = name_index.get(opp)
            h_form_src = [r for r in (opp_tl["rows"] if opp_tl else [])
                          if r[0] < d][-10:]
            a_form_src = form_rows  # 客队是该队
        else:
            h_form_src = form_rows
            opp_tl = name_index.get(opp)
            a_form_src = [r for r in (opp_tl["rows"] if opp_tl else [])
                          if r[0] < d][-10:]

        ko_dt = datetime.strptime(d, "%Y-%m-%d").replace(hour=12, tzinfo=TZ8)
        payload = {
            "home": h_name, "away": a_name,
            "home_team": h_name, "away_team": a_name,
            "kickoff_at": ko_dt.isoformat(),
            "snapshot_at": (ko_dt - timedelta(hours=1)).isoformat(),
            "home_recent": _mk_recent(h_form_src),
            "away_recent": _mk_recent(a_form_src),
            "neutral_site": neutral,
            "competition": comp,
        }
        try:
            asof = ko_dt - timedelta(hours=1)
            res = predict(payload, None, asof=asof)
            if res.get("status") == "insufficient_data":
                continue
            outcome = 0 if h_gf > h_ga else (1 if h_gf == h_ga else 2)
            results.append({
                "team": name, "date": d, "opp": opp, "venue": venue,
                "score": [h_gf, h_ga], "outcome": outcome,
                "probs": [res["p_home"], res["p_draw"], res["p_away"]],
                "grade": res["grade"],
            })
        except PredictError:
            continue
        except Exception:
            continue
    return results

def main():
    cands = json.load(open("/tmp/wf_candidates.json"))
    src = Titan007Source(request_delay=0.4)
    print("collecting histories...", flush=True)
    teams = collect_histories(src, cands, max_matches=50)
    json.dump({k: {"name": v["name"], "n": len(v["rows"])}
               for k, v in teams.items()},
              open("/tmp/wf_teams.json", "w"), ensure_ascii=False, indent=1)
    print(f"teams collected: {len(teams)}", flush=True)

    print("walk-forward...", flush=True)
    # 球队名 -> 时间线索引（对手 as-of 查询用）
    name_index = {}
    for key, team in teams.items():
        # 同名保留行数多的
        nm = team["name"]
        if nm not in name_index or len(team["rows"]) > len(name_index[nm]["rows"]):
            name_index[nm] = team
    all_results = []
    for j, (key, team) in enumerate(teams.items()):
        r = walkforward_team(key, team, name_index)
        all_results.extend(r)
        if j % 10 == 0:
            print(f"teams {j}/{len(teams)}, predictions={len(all_results)}",
                  flush=True)
    json.dump(all_results, open("/tmp/wf_form_results.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"DONE: {len(all_results)} predictions", flush=True)

main()
