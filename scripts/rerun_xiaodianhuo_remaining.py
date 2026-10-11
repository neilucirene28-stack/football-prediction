#!/usr/bin/env python3
"""按小店火 2026-09-29 竞足赛程：仅重跑 8 场未开赛（09-30 凌晨+08:00）。
用 v2.3 引擎（联赛级别修正），取 Titan007 最新赛前快照。输出 /tmp/xdh_remain.json。
注意：已完赛 7 场不重跑（赛后数据会污染赛前快照）。
"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.collector.sources.titan007 import (
    Titan007Source, _DETAIL_URL, _http_get, ENTRY_URL)
from engine.jingcai_predictor import predict
from engine.jingcai_batch import with_official_handicap, handicap_output
from engine.jingcai_calibration import load_form_only_config

# 剩余 8 场（09-30 02:45 欧国联 6 场 + 09-30 08:00 美国vs智利）
TARGETS = [
    ("苏格兰", "瑞士"), ("捷克", "英格兰"),
    ("斯洛文尼亚", "北马其顿"), ("斯洛伐克", "哈萨克斯坦"),
    ("卢森堡", "冰岛"), ("西班牙", "克罗地亚"),
    ("保加利亚", "爱沙尼亚"), ("美国", "智利"),
]

_CALIBRATION_CONFIG, _CALIBRATION_SELECTION = load_form_only_config()


def norm(s):
    s = (s or "").replace(" ", "").replace("FC", "").replace("MYFC", "MYF")
    for suf in ["城", "市"]:
        pass
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
    src = Titan007Source()
    ids = src._list_match_ids()
    print(f"扫描 {len(ids)} 个 Titan007 ID", flush=True)
    from concurrent.futures import ThreadPoolExecutor
    index = {}

    def fetch(mid):
        try:
            info = src.parse_detail(
                _http_get(_DETAIL_URL.format(mid=mid), referer=ENTRY_URL, timeout=15), mid)
            ko = info.get("kickoff_at", "") if info else ""
            if info and ko.startswith("2026-09-30"):
                return (mid, info["home_team"], info["away_team"])
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=15) as ex:
        for r in ex.map(fetch, ids):
            if r:
                index[(norm(r[1]), norm(r[2]))] = r[0]

    results = []
    for ht, at_ in TARGETS:
        mid = index.get((norm(ht), norm(at_)))
        if not mid:
            for (h, a), m in index.items():
                if name_match(ht, h) and name_match(at_, a):
                    mid = m
                    break
        if not mid:
            print(f"未找到: {ht} vs {at_}", flush=True)
            results.append({"home": ht, "away": at_, "status": "not_found"})
            continue
        try:
            info = src.parse_detail(
                _http_get(_DETAIL_URL.format(mid=mid), referer=ENTRY_URL, timeout=20), mid)
            m = src._build_match(info)
            p = dict(m)
            p["home"] = m["home_team"]
            p["away"] = m["away_team"]
            cfg = _CALIBRATION_CONFIG if not p.get("odds") else None
            res = predict(p, cfg)
            if res.get("status") == "insufficient_data":
                results.append({"home": ht, "away": at_, "status": "insufficient"})
                continue
            d = res["derivatives"]
            h1x2 = handicap_output(res)
            results.append({
                "kickoff": m["kickoff_at"], "competition": m["competition"],
                "home": m["home_team"], "away": m["away_team"],
                "neutral": bool(m.get("neutral_site")),
                "p_home": res["p_home"], "p_draw": res["p_draw"], "p_away": res["p_away"],
                "lambda_home": res["lambda_home"], "lambda_away": res["lambda_away"],
                "tier_adjust": res["lambda_notes"].get("tier_adjust"),
                "signals": res["signals"], "weights": res["weights"],
                "market": res.get("market"),
                "odds_open": m.get("opening_odds"),
                "odds_live": m.get("odds"),
                "asian": m.get("asian"), "ou": m.get("ou_line"),
                "completeness": res["completeness"], "grade": res["grade"],
                "top_scores": d["top_scores"][:5],
                "half_time": d.get("half_time"),
                "handicap_1x2": h1x2,
                "calibration_selection": _CALIBRATION_SELECTION,
                "d_asian": d.get("asian"), "d_ou": d.get("ou"),
                "total_goals": d.get("total_goals"),
                "confidence": res["confidence"],
                "status": "ok",
            })
            print(f"OK {ht} vs {at_}: {res['p_home']:.0%}/{res['p_draw']:.0%}/{res['p_away']:.0%} "
                  f"tier={res['lambda_notes'].get('tier_adjust')}", flush=True)
        except Exception as e:
            print(f"FAIL {ht} vs {at_}: {e}", flush=True)
            results.append({"home": ht, "away": at_, "status": f"error: {e}"})
    json.dump(results, open("/tmp/xdh_remain.json", "w"), ensure_ascii=False, indent=1)
    print(f"完成 {len(results)} 场 → /tmp/xdh_remain.json", flush=True)


if __name__ == "__main__":
    main()
