#!/usr/bin/env python3
"""按竞彩官方开售赛程（JSON）跑 v2.4 生产链路预测。

输入: /tmp/jingcai_today_fixtures.json
  [{"no": "周一001", "home": "主队", "away": "客队", "kickoff": "2026-10-05 18:00",
    "rq": 0, "odds": [2.10, 3.20, 3.10], "competition": "英超"}]
输出: /tmp/jingcai_today_1005_results.json（引擎原始结果 + 竞彩让球口径概率）
"""
import sys, json, re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.collector.sources.titan007 import (
    Titan007Source, _DETAIL_URL, _http_get, ENTRY_URL)
from engine.jingcai_predictor import predict
from engine.jingcai_batch import with_official_handicap, handicap_output
from engine.jingcai_calibration import load_form_only_config

FIXTURES = "/tmp/jingcai_today_fixtures.json"
OUT = "/tmp/jingcai_today_1005_results.json"
# 开球日期取自赛程（竞彩销售日跨自然日：10-05 开售的场次开球在 10-06 凌晨）

_CALIBRATION_CONFIG, _CALIBRATION_SELECTION = load_form_only_config()


def norm(s):
    s = (s or "").replace(" ", "").replace("　", "").replace("FC", "")
    s = re.sub(r"(U-?2[34]?|U2[34]|青年|预备队|B队|二队)$", "", s)
    return s


def name_match(target, cand):
    t, c = norm(target), norm(cand)
    if not t or not c:
        return False
    if t in c or c in t:
        return True
    if len(t) >= 2 and len(c) >= 2 and t[:2] == c[:2]:
        return True
    return False


def main():
    fixtures = json.load(open(FIXTURES))
    print(f"fixtures: {len(fixtures)}", flush=True)
    ko_dates = {fx["kickoff"][:10] for fx in fixtures if fx.get("kickoff")}
    print(f"kickoff dates: {sorted(ko_dates)}", flush=True)
    src = Titan007Source()
    ids = src._list_match_ids()
    print(f"扫描 {len(ids)} 个 Titan007 ID", flush=True)
    index = {}

    def fetch(mid):
        try:
            html = _http_get(_DETAIL_URL.format(mid=mid), referer=ENTRY_URL, timeout=15)
            info = src.parse_detail(html, mid)
            ko = info.get("kickoff_at", "") if info else ""
            if info and ko[:10] in ko_dates:
                return (mid, info["home_team"], info["away_team"], ko)
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=15) as ex:
        for r in ex.map(fetch, ids):
            if r:
                index[(norm(r[1]), norm(r[2]))] = (r[0], r[3])
    print(f"场次索引 {len(index)} 场", flush=True)

    results = []
    for fx in fixtures:
        ht, at = fx["home"], fx["away"]
        mid = None
        for (h, a), (m, ko) in index.items():
            if name_match(ht, h) and name_match(at, a):
                mid = m
                break
        if not mid:
            print(f"未匹配: {fx['no']} {ht} vs {at}", flush=True)
            results.append({"shop": fx, "status": "not_found"})
            continue
        try:
            html = _http_get(_DETAIL_URL.format(mid=mid), referer=ENTRY_URL, timeout=20)
            info = src.parse_detail(html, mid)
            m = src._build_match(info)
            if not m:
                results.append({"shop": fx, "status": "build_failed"})
                continue
            p = dict(m)
            p["home"] = m["home_team"]
            p["away"] = m["away_team"]
            p = with_official_handicap(p, fx)
            cfg = _CALIBRATION_CONFIG if not p.get("odds") else None
            res = predict(p, cfg)
            if res.get("status") == "insufficient_data":
                results.append({"shop": fx, "status": "insufficient",
                                "reason": res.get("reason")})
                continue
            d = res["derivatives"]
            h1x2 = handicap_output(res, include_rq_alias=True)
            cards = d.get("cards") or {}
            results.append({
                "shop": fx, "titan_matchid": mid,
                "kickoff": m["kickoff_at"], "competition": m["competition"],
                "home": m["home_team"], "away": m["away_team"],
                "p_home": res["p_home"], "p_draw": res["p_draw"], "p_away": res["p_away"],
                "lambda_home": res["lambda_home"], "lambda_away": res["lambda_away"],
                "signals": res["signals"], "weights": res["weights"],
                "market": res.get("market"),
                "odds_live": m.get("odds"), "odds_open": m.get("opening_odds"),
                "jingcai_odds": fx.get("odds"),
                "completeness": res["completeness"], "grade": res["grade"],
                "top_scores": d["top_scores"][:5],
                "half_time": d.get("half_time"),
                "handicap_1x2": h1x2,
                "calibration_selection": _CALIBRATION_SELECTION,
                "total_goals": d.get("total_goals"),
                "cards": cards if cards else None,
                "confidence": res["confidence"],
                "confidence_score": res.get("confidence_score"),
                "warnings": res.get("warnings"),
                "status": "ok",
            })
            print(f"OK {fx['no']} {ht} vs {at}: "
                  f"{res['p_home']:.0%}/{res['p_draw']:.0%}/{res['p_away']:.0%} "
                  f"grade={res['grade']} conf={res['confidence']} "
                  f"warn={res.get('warnings')}", flush=True)
        except Exception as e:
            print(f"FAIL {fx['no']} {ht} vs {at}: {e}", flush=True)
            results.append({"shop": fx, "status": f"error: {e}"})
    json.dump(results, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"完成 {len(results)} 场 → {OUT}", flush=True)


if __name__ == "__main__":
    main()
