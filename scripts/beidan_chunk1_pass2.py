#!/usr/bin/env python3
"""北单 chunk1 第二遍：对跳过的场次重试 7M（fast_search 双尝试 + 更多关键词变体）。"""
import sys, json, time, re
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/collector/sources")
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/scripts")

from beidan_chunk1_run import (qim_fuzzy_mid, qim_team_recent, predict_one,
                               fast_search, _pair_score, s2t)
from qim import norm_team


def strip_suffix(name):
    n = name
    for suf in ["青年队", "青年", "B队", "二队", "预备队", "U23", "U21"]:
        if n.endswith(suf):
            n = n[: -len(suf)]
    n = re.sub(r"\s*FC\s*$", "", n, flags=re.I).strip()
    return n


def fuzzy_mid_v2(home, away):
    from qim import _is_senior
    cands, kw_src = {}, {}
    base = [(home, "home"), (away, "away"),
            (strip_suffix(home), "home"), (strip_suffix(away), "away"),
            (home[:2], "home"), (away[:2], "away"),
            (s2t(home), "home"), (s2t(away), "away")]
    # 去重保持顺序
    seen_kw, kws = set(), []
    for kw, src in base:
        if kw and kw not in seen_kw:
            seen_kw.add(kw)
            kws.append((kw, src))
    for kw, src in kws:
        for attempt in range(2):
            rs = fast_search(kw)
            if rs:
                for r in rs:
                    if not _is_senior(r):
                        continue
                    cands[r["mid"]] = r
                    kw_src.setdefault(r["mid"], set()).add(src)
                break
            time.sleep(0.5)
        time.sleep(0.2)
    best, best_sc = None, -1.0
    for mid, r in cands.items():
        sh = _pair_score(home, r["home"])
        sa = _pair_score(away, r["away"])
        sh2 = _pair_score(home, r["away"])
        sa2 = _pair_score(away, r["home"])
        if sh + sa >= sh2 + sa2:
            sc, mn, mx = sh + sa, min(sh, sa), max(sh, sa)
        else:
            sc, mn, mx = sh2 + sa2, min(sh2, sa2), max(sh2, sa2)
        src = kw_src.get(mid, set())
        ok = (sc >= 1.0 and mn >= 0.15) or \
             (("home" in src or "away" in src) and mx >= 0.4 and mn >= 0.2)
        if ok and sc > best_sc:
            best, best_sc = mid, sc
    return best


def main():
    results = json.load(open("/tmp/beidan_result1.json", encoding="utf-8"))
    matches = {m["seq"]: m for m in
               json.load(open("/tmp/beidan_chunk1.json", encoding="utf-8"))}
    fixed = 0
    for r in results:
        if r["status"] == "ok":
            continue
        if "冰球" in (r.get("reason") or ""):
            continue
        m = matches[r["seq"]]
        print(f"重试 seq={r['seq']} {m['home']}vs{m['away']}...", flush=True)
        # monkeypatch 本轮的 fuzzy
        import beidan_chunk1_run as mod
        mod.qim_fuzzy_mid = fuzzy_mid_v2
        t0 = time.time()
        nr = predict_one(m)
        print(f"  -> {nr['status']} ({nr.get('form_source') or nr.get('reason','')[:60]}) "
              f"{time.time()-t0:.1f}s", flush=True)
        if nr["status"] == "ok":
            r.clear()
            r.update(nr)
            fixed += 1
        else:
            r["reason"] = nr.get("reason", r.get("reason"))
    ok = sum(1 for x in results if x["status"] == "ok")
    print(f"\n第二遍修复 {fixed} 场；现成功 {ok}/{len(results)}")
    json.dump(results, open("/tmp/beidan_result1.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
