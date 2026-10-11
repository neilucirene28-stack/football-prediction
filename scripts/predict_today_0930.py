#!/usr/bin/env python3
"""按小店火 2026-09-30 竞足赛程（2 场：亚运男足）跑预测。输出 /tmp/xdh_today_0930.json。"""
import sys, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.collector.sources.titan007 import (
    Titan007Source, _DETAIL_URL, _http_get, ENTRY_URL)
from engine.jingcai_predictor import predict
from engine.jingcai_batch import with_official_handicap, handicap_output
from engine.jingcai_calibration import load_form_only_config

# 小店火 2026-09-30 竞足页签 2 场（浏览器 11:16 提取，全部未开赛）
# 1. 14:00 韩国亚 vs 中国亚  编号3001  让球0  赔率 1.13/5.50/16.00
# 2. 18:30 乌兹别亚 vs 日本亚 编号3002 让球0  赔率 5.05/3.50/1.55
TARGETS = [("韩国亚", "中国亚"), ("乌兹别亚", "日本亚")]
SHOP = {
    ("韩国亚", "中国亚"): {"no": "3001", "kickoff": "14:00", "odds": (1.13, 5.50, 16.00)},
    ("乌兹别亚", "日本亚"): {"no": "3002", "kickoff": "18:30", "odds": (5.05, 3.50, 1.55)},
}

_CALIBRATION_CONFIG, _CALIBRATION_SELECTION = load_form_only_config()


def norm(s):
    s = (s or "").replace(" ", "").replace("FC", "")
    # 去掉亚运/U23/国奥等年龄队后缀，保留核心国名
    s = re.sub(r"(亚运|亚|U-?2[34]?|国奥|奥运|青年|U2[34])$", "", s)
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
                return (mid, info["home_team"], info["away_team"], ko)
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=15) as ex:
        for r in ex.map(fetch, ids):
            if r:
                index[(norm(r[1]), norm(r[2]))] = (r[0], r[3])
    print(f"2026-09-30 场次索引 {len(index)} 场", flush=True)

    results = []
    for ht, at_ in TARGETS:
        mid = None
        for (h, a), (m, ko) in index.items():
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
                results.append({"home": ht, "away": at_, "status": "insufficient",
                                "reason": res.get("reason")})
                continue
            d = res["derivatives"]
            h1x2 = handicap_output(res)
            cards = d.get("cards") or {}
            results.append({
                "shop": SHOP[(ht, at_)],
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
                "cards": cards if cards else None,
                "confidence": res["confidence"], "warnings": res.get("warnings"),
                "status": "ok",
            })
            print(f"OK {ht} vs {at_}: {res['p_home']:.0%}/{res['p_draw']:.0%}/{res['p_away']:.0%} "
                  f"grade={res['grade']} cards={cards.get('status') if cards else 'none'}", flush=True)
        except Exception as e:
            print(f"FAIL {ht} vs {at_}: {e}", flush=True)
            results.append({"home": ht, "away": at_, "status": f"error: {e}"})
    json.dump(results, open("/tmp/xdh_today_0930.json", "w"), ensure_ascii=False, indent=1)
    print(f"完成 {len(results)} 场 → /tmp/xdh_today_0930.json", flush=True)


if __name__ == "__main__":
    main()
