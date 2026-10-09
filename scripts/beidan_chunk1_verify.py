#!/usr/bin/env python3
"""核验 7M 来源的场次：用 senior 过滤重新跑，记录 mid 并对比预测是否稳定。"""
import sys, json, time
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/collector/sources")
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/scripts")

from beidan_chunk1_run import predict_one

results = json.load(open("/tmp/beidan_result1.json", encoding="utf-8"))
matches = {m["seq"]: m for m in
           json.load(open("/tmp/beidan_chunk1.json", encoding="utf-8"))}
targets = [r for r in results
           if r["status"] == "ok" and r.get("form_source") == "7m"]
print(f"待核验 {len(targets)} 场", flush=True)
report = []
for r in targets:
    m = matches[r["seq"]]
    t0 = time.time()
    try:
        nr = predict_one(m)
    except Exception as e:
        report.append({"seq": r["seq"], "verify": f"error:{str(e)[:80]}"})
        print(f"seq={r['seq']} ERROR {str(e)[:60]}", flush=True)
        continue
    dt = time.time() - t0
    if nr["status"] != "ok":
        report.append({"seq": r["seq"], "verify": "now_skipped",
                       "reason": nr.get("reason", "")[:80]})
        print(f"seq={r['seq']} NOW_SKIPPED {nr.get('reason','')[:60]} {dt:.0f}s",
              flush=True)
        continue
    dp = lambda k: round(abs(nr[k] - r[k]), 4)
    drift = max(dp("p_home"), dp("p_draw"), dp("p_away"))
    same_pick = (nr["胜平负预测"] == r["胜平负预测"])
    report.append({"seq": r["seq"], "verify": "ok",
                   "form_mid": nr.get("form_mid"),
                   "max_prob_drift": drift, "same_wdl_pick": same_pick})
    print(f"seq={r['seq']} mid={nr.get('form_mid')} drift={drift} "
          f"same_pick={same_pick} {dt:.0f}s", flush=True)
    # 用核验后的结果更新（带 senior 过滤 + mid 记录）
    r.clear()
    r.update(nr)

json.dump(results, open("/tmp/beidan_result1.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
json.dump(report, open("/tmp/beidan_verify.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("核验完成，已更新 /tmp/beidan_result1.json", flush=True)
