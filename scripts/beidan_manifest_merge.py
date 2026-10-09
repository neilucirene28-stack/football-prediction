#!/usr/bin/env python3
"""北单全池manifest合并。

将各chunk的运行清单合并为全池manifest。

用法:
    python3 scripts/beidan_manifest_merge.py \\
        --chunks /tmp/beidan_manifest1.json /tmp/beidan_manifest2.json \\
        --schedule data/predictions/2026-10-09-beidan.json \\
        --lottery-no 26103

规则:
- 按 (lottery_no, seq) 去重；重复时保留 generated_at 最早的一条，并记录冲突。
- 对照开售赛程集合（--schedule），报告覆盖率 = 合并场数 / 赛程总场数。
- 输出带 run_id 的只追加文件 data/manifests/beidan/<lottery_no>/<run_id>.json，
  不覆盖先前run。run_id = UTC时间戳，同一秒内加序号保证唯一。
- 不为凑数赛后重跑冒充赛前：本脚本只合并已有清单，不生成新预测。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

OUT_BASE = "data/manifests/beidan"


def load_chunk(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="合并北单chunk清单为全池manifest")
    ap.add_argument("--chunks", nargs="+", required=True, help="chunk清单JSON文件")
    ap.add_argument("--schedule", default=None,
                    help="开售赛程JSON（含全部场次，用于覆盖率）")
    ap.add_argument("--lottery-no", required=True, help="期号")
    ap.add_argument("--run-id", default=None, help="手动指定run_id（默认UTC时间戳）")
    args = ap.parse_args()

    lottery_no = args.lottery_no
    merged = {}          # (lottery_no, seq) -> match record
    conflicts = []       # 重复的 (lottery_no, seq)
    chunk_stats = []

    for path in args.chunks:
        chunk = load_chunk(path)
        c_lottery = chunk.get("lottery_no", "unknown")
        if c_lottery != lottery_no:
            print(f"警告: {path} 期号 {c_lottery} 与目标 {lottery_no} 不一致，已跳过",
                  file=sys.stderr)
            continue
        n_ok = n_sk = 0
        for m in chunk.get("matches", []):
            key = (c_lottery, str(m.get("seq")))
            if key in merged:
                # 保留 generated_at 最早的一条
                old = merged[key]
                old_t = old.get("_chunk_generated_at", "")
                new_t = chunk.get("generated_at", "")
                conflicts.append({"key": key, "chunk": path,
                                  "kept": "old" if old_t <= new_t else "new"})
                if new_t and (not old_t or new_t < old_t):
                    merged[key] = {**m, "_chunk_generated_at": new_t,
                                   "_chunk_source": path}
                continue
            merged[key] = {**m, "_chunk_generated_at": chunk.get("generated_at", ""),
                           "_chunk_source": path}
            if m.get("status") == "ok":
                n_ok += 1
            else:
                n_sk += 1
        chunk_stats.append({"path": path, "ok": n_ok, "skipped": n_sk})

    # 覆盖率对照
    schedule_total = None
    coverage = None
    if args.schedule:
        with open(args.schedule, encoding="utf-8") as f:
            sched = json.load(f)
        sched_matches = sched.get("matches", sched) if isinstance(sched, dict) else sched
        sched_keys = {(lottery_no, str(m.get("seq") or m.get("no")))
                      for m in sched_matches}
        schedule_total = len(sched_keys)
        merged_keys = set(merged.keys())
        coverage = {
            "schedule_total": schedule_total,
            "merged_total": len(merged_keys),
            "covered": len(merged_keys & sched_keys),
            "missing_from_merge": sorted(sched_keys - merged_keys),
            "extra_not_in_schedule": sorted(merged_keys - sched_keys),
            "coverage_rate": (len(merged_keys & sched_keys) / schedule_total
                              if schedule_total else None),
        }

    # skip原因汇总
    from collections import Counter
    reasons = Counter(m.get("reason", "?") for m in merged.values()
                      if m.get("status") != "ok")
    n_ok = sum(1 for m in merged.values() if m.get("status") == "ok")

    # run_id：UTC时间戳，防碰撞
    if args.run_id:
        run_id = args.run_id
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = os.path.join(OUT_BASE, lottery_no)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{run_id}.json")
    suffix = 1
    while os.path.exists(out_path):
        out_path = os.path.join(out_dir, f"{run_id}_{suffix}.json")
        suffix += 1
    # 最终run_id取文件名
    run_id = os.path.splitext(os.path.basename(out_path))[0]

    manifest = {
        "scope": "full_pool",
        "lottery_no": lottery_no,
        "run_id": run_id,
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "chunks": chunk_stats,
        "conflicts": conflicts,
        "pool_total": len(merged),
        "predicted_ok": n_ok,
        "skipped": len(merged) - n_ok,
        "skip_reasons": dict(reasons),
        "coverage": coverage,
        "matches": [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for _, m in sorted(merged.items())
        ],
    }
    # 原子写入：临时文件 + rename
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, out_path)

    print(f"全池manifest已写: {out_path}")
    print(f"  合并场数: {len(merged)} (ok={n_ok}, skipped={len(merged)-n_ok})")
    print(f"  冲突: {len(conflicts)}")
    if coverage:
        print(f"  赛程覆盖: {coverage['covered']}/{coverage['schedule_total']} "
              f"({coverage['coverage_rate']:.1%})" if coverage['coverage_rate'] is not None else "")
        if coverage["missing_from_merge"]:
            print(f"  未覆盖场次: {len(coverage['missing_from_merge'])}")
    print("  注意: 本manifest只合并已有chunk清单，不生成新预测；不为凑数赛后重跑。")


if __name__ == "__main__":
    main()
