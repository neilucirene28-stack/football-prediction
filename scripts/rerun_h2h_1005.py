#!/usr/bin/env python3
"""H2H 注入重跑：用澳客 h2h 数据，按引擎 predictor.py 第223-230行精确公式调整 λ，
再用引擎自身函数（score_matrix/match_probs/ensemble/derivatives）重算全部输出。

输入:
  /tmp/jingcai_today_1005_enriched.json（002-007）
  /tmp/jingcai_001.json（001）
  data/daily/2026-10-05/okooo_500_matches.json（h2h）
输出:
  /tmp/jingcai_1005_h2h_rerun.json
"""
import sys, json
from datetime import datetime
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from engine.poisson import (
    score_matrix, match_probs, handicap_1x2, top_scores,
    total_goals_distribution, expected_total_goals, main_goal_interval,
    half_time_probs,
)
from engine.fusion import ensemble

RHO = -0.13
HT_FACTOR = 0.44

ENRICHED = "/tmp/jingcai_today_1005_enriched.json"
M001 = "/tmp/jingcai_001.json"
H2H = "/home/hatch/workspace/football-prediction-v2/data/daily/2026-10-05/okooo_500_matches.json"
OUT = "/tmp/jingcai_1005_h2h_rerun.json"


def parse_score(s):
    h, a = s.strip().split("-")
    return int(h), int(a)


def h2h_adjust(home, away, h2h_list):
    """精确复刻 predictor.py 223-230。h2h_list 按日期升序（最老在前），引擎取 [-3:]。"""
    rows = []
    for r in sorted(h2h_list, key=lambda x: x.get("date", "")):
        try:
            gh, ga = parse_score(r["score"])
        except Exception:
            continue
        rh, ra = r.get("home", ""), r.get("away", "")
        if home in rh or rh in home:
            gf, gaa = gh, ga
        elif home in ra or ra in home:
            gf, gaa = ga, gh
        else:
            continue  # 队名对不上，跳过（不编造）
        rows.append({"gf": gf, "ga": gaa, "date": r.get("date")})
    if not rows:
        return 0.0, []
    gd = sum(x["gf"] - x["ga"] for x in rows[-3:])
    adj = max(min(gd * 0.01, 0.03), -0.03)
    return adj, rows[-3:]


def rerun(no, home, away, rq, lam_h, lam_a, p_market, weights, h2h_list):
    adj, used = h2h_adjust(home, away, h2h_list)
    lh2 = lam_h * (1 + adj)
    la2 = lam_a * (1 - adj)
    mx = score_matrix(lh2, la2, rho=RHO)
    p_model = match_probs(mx)
    pf = ensemble({"model": p_model, "market": tuple(p_market), "elo": None},
                  {"model": weights["model"], "market": weights["market"],
                   "elo": weights.get("elo", 0.0)})
    p_home, p_draw, p_away = (round(p, 4) for p in pf)
    h, dr, a = handicap_1x2(mx, int(rq))
    ht = half_time_probs(lh2, la2, ht_factor=HT_FACTOR, rho=RHO)
    tg = {str(k): round(v, 4) for k, v in total_goals_distribution(mx).items()}
    interval, interval_p = main_goal_interval(mx)
    # 冷门比分：第二可能结果中概率最高的比分（复刻 predictor.py 358-368）
    order = sorted(range(3), key=lambda i: pf[i], reverse=True)
    uo = order[1]
    cands = [(s, round(p, 4)) for s, p in top_scores(mx, n=12)
             if (0 if int(s.split("-")[0]) > int(s.split("-")[1])
                 else (1 if s.split("-")[0] == s.split("-")[1] else 2)) == uo]
    return {
        "no": no, "home": home, "away": away, "rq": rq,
        "h2h_adjust": round(adj, 4), "h2h_used": used,
        "lambda_home": round(lh2, 4), "lambda_away": round(la2, 4),
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "p_model": [round(p, 4) for p in p_model],
        "handicap_1x2": {"p_home": round(h, 4), "p_draw": round(dr, 4),
                         "p_away": round(a, 4)},
        "half_time": {"p_home": round(ht["p_home"], 4),
                      "p_draw": round(ht["p_draw"], 4),
                      "p_away": round(ht["p_away"], 4)},
        "top_scores": [{"score": s, "prob": round(p, 4)}
                       for s, p in top_scores(mx, n=5)],
        "total_goals": tg,
        "expected_goals": round(expected_total_goals(mx), 2),
        "main_goal_interval": {"label": interval, "prob": round(interval_p, 4)},
        "upset_score": {"score": cands[0][0], "prob": cands[0][1]} if cands else None,
    }


def main():
    h2h_data = {m["no"]: m for m in json.load(open(H2H, encoding="utf-8"))["matches"]}
    out = []
    # 001
    r = json.load(open(M001, encoding="utf-8"))["res"]
    m1 = h2h_data["周一001"]
    out.append(rerun("周一001", "塞浦路斯", "拉脱维亚", -1,
                     r["lambda_home"], r["lambda_away"],
                     r["signals"]["market"], r["weights"], m1["h2h"]))
    # 002-007
    for m in json.load(open(ENRICHED, encoding="utf-8")):
        sh = m["shop"]
        no = sh["no"].replace("周二", "周一")
        mh = h2h_data[no]
        out.append(rerun(no, m["home"], m["away"], sh["rq"],
                         m["lambda_home"], m["lambda_away"],
                         m["signals"]["market"], m["weights"], mh["h2h"]))
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for o in out:
        print(f"{o['no']} {o['home']}vs{o['away']}: h2h_adj={o['h2h_adjust']:+.3f} "
              f"λ={o['lambda_home']}/{o['lambda_away']} "
              f"p={o['p_home']:.3f}/{o['p_draw']:.3f}/{o['p_away']:.3f} "
              f"让球={o['handicap_1x2']['p_home']:.3f}/{o['handicap_1x2']['p_draw']:.3f}/{o['handicap_1x2']['p_away']:.3f}")
    print("→", OUT)


if __name__ == "__main__":
    main()
