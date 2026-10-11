#!/usr/bin/env python3
"""竞彩 2026-10-05 开售 6 场（欧国联）：ESPN 近况 + Titan007 欧指市场 → v2.4 引擎。

输入: /tmp/jingcai_today_fixtures.json, /tmp/nt_form_espn.json
输出: /tmp/jingcai_today_1005_enriched.json

缺失明确标记：亚盘/大小球（vip.titan007.com 本机网络不通）、阵容伤停、H2H、xG。
"""
import sys, json
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from collector.collector.sources.titan007 import (
    Titan007Source, _DETAIL_URL, _http_get, ENTRY_URL)
from engine.jingcai_predictor import predict
from engine.poisson import handicap_1x2, score_matrix

FIXTURES = "/tmp/jingcai_today_fixtures.json"
FORM = "/tmp/nt_form_espn.json"
OUT = "/tmp/jingcai_today_1005_enriched.json"

# Titan007 matchid（已逐场核验主客队）
MIDS = {
    "周二002": "2981453",  # 法国 vs 比利时
    "周二003": "2981503",  # 罗马尼亚 vs 瑞典
    "周二004": "2981454",  # 意大利 vs 土耳其
    "周二005": "2981502",  # 北爱尔兰 vs 格鲁吉亚
    "周二006": "2981547",  # 黑山 vs 亚美尼亚
    "周二007": "2981504",  # 波黑 vs 波兰
}


def main():
    fixtures = {f["no"]: f for f in json.load(open(FIXTURES))}
    form = json.load(open(FORM))
    src = Titan007Source()
    results = []
    for no, fx in fixtures.items():
        mid = MIDS[no]
        try:
            html = _http_get(_DETAIL_URL.format(mid=mid), referer=ENTRY_URL, timeout=20)
            info = src.parse_detail(html, mid)
            if not info:
                results.append({"shop": fx, "status": "detail_parse_failed"}); continue
            m = src._build_match(info)
            # 校验主客队对上
            if not (fx["home"] in m["home_team"] and fx["away"] in m["away_team"]):
                results.append({"shop": fx, "status": "team_mismatch",
                                "titan": f"{m['home_team']} vs {m['away_team']}"})
                continue
            p = dict(m)
            p["home"] = m["home_team"]; p["away"] = m["away_team"]
            # 注入 ESPN 近况（analysis 页被墙，用 ESPN 替代，来源明确标记）
            hr = form.get(fx["home"], []); ar = form.get(fx["away"], [])
            p["home_recent"] = hr; p["away_recent"] = ar
            p.setdefault("raw", {})["form_source"] = "ESPN uefa.nations/fifa.world/fifa.friendly"
            res = predict(p, None)
            if res.get("status") == "insufficient_data":
                results.append({"shop": fx, "status": "insufficient",
                                "reason": res.get("reason"),
                                "completeness": res.get("completeness"),
                                "grade": res.get("grade")})
                continue
            d = res["derivatives"]
            rq = fx.get("rq")
            h1x2 = None
            if rq is not None:
                mx = score_matrix(res["lambda_home"], res["lambda_away"])
                h, dr, a = handicap_1x2(mx, int(rq))
                h1x2 = {"rq": int(rq), "p_home": round(h, 4),
                        "p_draw": round(dr, 4), "p_away": round(a, 4)}
            results.append({
                "shop": fx, "titan_matchid": mid,
                "kickoff": m["kickoff_at"], "competition": m["competition"],
                "home": m["home_team"], "away": m["away_team"],
                "form_source": "ESPN", "n_home_recent": len(hr), "n_away_recent": len(ar),
                "p_home": res["p_home"], "p_draw": res["p_draw"], "p_away": res["p_away"],
                "lambda_home": res["lambda_home"], "lambda_away": res["lambda_away"],
                "signals": res["signals"], "weights": res["weights"],
                "market": res.get("market"),
                "odds_live": m.get("odds"), "odds_open": m.get("opening_odds"),
                "jingcai_handicap_sp": fx.get("odds"),
                "missing": ["亚盘", "大小球", "阵容/伤停", "H2H", "xG"],
                "completeness": res["completeness"], "grade": res["grade"],
                "top_scores": d["top_scores"][:5],
                "half_time": d.get("half_time"),
                "handicap_1x2": h1x2,
                "total_goals": d.get("total_goals"),
                "confidence": res["confidence"],
                "confidence_score": res.get("confidence_score"),
                "warnings": res.get("warnings"),
                "status": "ok",
            })
            print(f"OK {no} {fx['home']} vs {fx['away']}: "
                  f"{res['p_home']:.1%}/{res['p_draw']:.1%}/{res['p_away']:.1%} "
                  f"grade={res['grade']} conf={res['confidence']} warn={res.get('warnings')}",
                  flush=True)
        except Exception as e:
            print(f"FAIL {no}: {e}", flush=True)
            results.append({"shop": fx, "status": f"error: {e}"})
    json.dump(results, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"完成 {len(results)} 场 → {OUT}", flush=True)


if __name__ == "__main__":
    main()
