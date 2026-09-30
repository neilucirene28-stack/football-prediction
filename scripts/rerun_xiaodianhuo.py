#!/usr/bin/env python3
"""按小店火 2026-09-29 竞足赛程（14 场未开赛）重跑预测。
用修复后的引擎（含联赛级别修正）。输出 JSON 到 /tmp/xdh_rerun.json。
"""
import sys, json
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from collector.collector.sources.titan007 import (
    Titan007Source, _DETAIL_URL, _http_get, ENTRY_URL)
from engine.predictor import predict
from engine.poisson import handicap_1x2, score_matrix

# 小店火 2026-09-29 竞足页签 14 场未开赛（朝鲜女足vs中国女足已开赛，排除）
TARGETS = [
    ("澳大利亚", "巴西"), ("藤枝MYFC", "大阪樱花"),
    ("枥木城", "广岛三箭"), ("富山胜利", "浦和红钻"),
    ("山形山神", "横滨水手"), ("日本女足", "韩国女足"),
    ("苏格兰", "瑞士"), ("捷克", "英格兰"),
    ("斯洛文尼亚", "北马其顿"), ("斯洛伐克", "哈萨克斯坦"),
    ("卢森堡", "冰岛"), ("西班牙", "克罗地亚"),
    ("保加利亚", "爱沙尼亚"), ("美国", "智利"),
]

try:
    _cal = json.load(open("/home/hatch/workspace/football-prediction-v2/engine/calibration.json"))
    _PLATT = {k: tuple(v) for k, v in _cal["form_only"].items()}
except Exception:
    _PLATT = None


def norm(s):
    s = (s or "").replace(" ", "").replace("FC", "").replace("MYFC", "MYF")
    # 去掉常见后缀/前缀差异
    for suf in ["城", "市"]:
        pass
    return s


def name_match(target, cand):
    """模糊队名匹配：互相包含或核心词重合"""
    t, c = norm(target), norm(cand)
    if not t or not c:
        return False
    if t in c or c in t:
        return True
    # 枥木城 vs 枥木市FC 这类：前两字相同
    if len(t) >= 2 and len(c) >= 2 and t[:2] == c[:2]:
        return True
    return False


def main():
    src = Titan007Source()
    ids = src._list_match_ids()
    print(f"扫描 {len(ids)} 个 Titan007 ID", flush=True)
    # 先建 (主队,客队)->mid 索引
    from concurrent.futures import ThreadPoolExecutor
    index = {}

    def fetch(mid):
        try:
            info = src.parse_detail(
                _http_get(_DETAIL_URL.format(mid=mid), referer=ENTRY_URL, timeout=15), mid)
            ko = info.get("kickoff_at", "") if info else ""
            # 小店火 09-29 页签含 09-30 凌晨场（02:45）+ 08:00 场
            if info and (ko.startswith("2026-09-29") or ko.startswith("2026-09-30")):
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
        # 模糊匹配
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
            cfg = {"platt": _PLATT} if (_PLATT and not p.get("odds")) else None
            res = predict(p, cfg)
            if res.get("status") == "insufficient_data":
                results.append({"home": ht, "away": at_, "status": "insufficient"})
                continue
            d = res["derivatives"]
            h1x2 = None
            ah = (d.get("asian") or {}).get("handicap")
            if ah is not None:
                line = int(round(ah))
                mx = score_matrix(res["lambda_home"], res["lambda_away"])
                h, dr, a = handicap_1x2(mx, line)
                h1x2 = {"line": line, "p_home": round(h, 4),
                        "p_draw": round(dr, 4), "p_away": round(a, 4)}
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
    json.dump(results, open("/tmp/xdh_rerun.json", "w"), ensure_ascii=False, indent=1)
    print(f"完成 {len(results)} 场 → /tmp/xdh_rerun.json", flush=True)


if __name__ == "__main__":
    main()
