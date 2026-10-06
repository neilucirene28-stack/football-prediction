#!/usr/bin/env python3
"""Phase 2 回填：合并 12 批 browser task 结果并校验 as-of 无泄漏。

用法: python3 scripts/merge_phase2.py
输入: data/phase2_batches/result_*.json
输出: data/phase2_history.json
"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
BATCH_DIR = DATA / "phase2_batches"

# asof 参照：phase2_matches.json 的 kickoff 日期
matches = json.load(open(DATA / "phase2_matches.json", encoding="utf-8"))
asof_map = {m["mido"]: m["kickoff"][:10] for m in matches}

merged = []
failed = []      # (mido, reason)
leak_errors = []  # as-of 违规
seen = set()

for i in range(1, 13):
    p = BATCH_DIR / f"result_{i:02d}.json"
    if not p.exists():
        print(f"缺失: {p.name}", flush=True)
        continue
    try:
        batch = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print(f"解析失败: {p.name}: {e}", flush=True)
        continue
    for rec in batch:
        mido = str(rec.get("mido", ""))
        if not mido:
            continue
        if mido in seen:
            print(f"重复 mido: {mido}（{p.name}），跳过", flush=True)
            continue
        seen.add(mido)
        if rec.get("error"):
            failed.append((mido, rec["error"]))
            continue
        asof = asof_map.get(mido)
        if not asof:
            failed.append((mido, "mido 不在输入清单"))
            continue
        # as-of 校验：form 里每场 date 必须 < asof
        ok = True
        for side in ("home_form", "away_form"):
            for g in rec.get(side) or []:
                d = g.get("date", "")
                if not d or d >= asof:
                    leak_errors.append((mido, side, d, asof))
                    ok = False
        if not ok:
            failed.append((mido, "as-of 违规（form 含未来比赛），已剔除"))
            continue
        merged.append({
            "mido": mido,
            "hg": rec.get("hg"),
            "ag": rec.get("ag"),
            "ht_hg": rec.get("ht_hg"),
            "ht_ag": rec.get("ht_ag"),
            "home_form": rec.get("home_form") or [],
            "away_form": rec.get("away_form") or [],
        })

# 未覆盖的 mido
missing = [m for m in asof_map if m not in seen]

with open(DATA / "phase2_history.json", "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=1)

print("=" * 50)
print(f"成功: {len(merged)} 场 -> {DATA / 'phase2_history.json'}")
print(f"失败: {len(failed)} 场")
for mido, reason in failed:
    print(f"  {mido}: {reason}")
print(f"未覆盖（无结果文件）: {len(missing)} 场")
for mido in missing[:20]:
    print(f"  {mido}")
if len(missing) > 20:
    print(f"  ... 另 {len(missing) - 20} 场")
sys.exit(0 if not missing and not leak_errors else 2)
