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
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

OUT_BASE = "data/manifests/beidan"

# run_id 只允许安全字符：字母数字下划线连字符（防路径遍历）
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# 权威期号场数：已核验的开售全池场数。schedule_total 必须等于此值才能考虑 full_pool。
# 26103 = 193场（data/predictions/2026-10-09-beidan.json，2026-10-09核验）。
AUTHORITATIVE_TOTALS = {
    "26103": 193,
}


def validate_run_id(run_id):
    """校验手工run_id；非法则返回错误信息，否则返回None。"""
    if not run_id or not RUN_ID_RE.match(run_id):
        return (f"非法run_id {run_id!r}：只允许字母、数字、下划线、连字符，"
                f"不能为空")
    if run_id in (".", ".."):
        return f"非法run_id {run_id!r}"
    return None


def atomic_write_excl(path, data_bytes):
    """O_EXCL原子独占写入；文件已存在则抛FileExistsError，不覆盖。"""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        os.write(fd, data_bytes)
        os.fsync(fd)
    finally:
        os.close(fd)


def load_chunk(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha256_file(path):
    """计算文件SHA256（schedule证据）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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

    # 覆盖率对照 + schedule证据
    schedule_total = None
    coverage = None
    schedule_evidence = None
    if args.schedule:
        sched_sha = sha256_file(args.schedule)
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
        # schedule证据：SHA256、来源、期号、权威场数核验
        sched_dict = sched if isinstance(sched, dict) else {}
        expected_total = AUTHORITATIVE_TOTALS.get(lottery_no)
        schedule_evidence = {
            "sha256": sched_sha,
            "lottery_no": lottery_no,
            "source_url": sched_dict.get("source_url"),
            "fetched_at": sched_dict.get("fetched_at"),
            "expected_total": expected_total,   # 权威场数；未知期号为null
            "schedule_total": schedule_total,
            "schedule_complete": (expected_total is not None
                                  and schedule_total == expected_total),
        }

    # skip原因汇总
    from collections import Counter
    reasons = Counter(m.get("reason", "?") for m in merged.values()
                      if m.get("status") != "ok")
    n_ok = sum(1 for m in merged.values() if m.get("status") == "ok")

    # run_id：手工指定必须过安全校验；默认UTC时间戳（含微秒防碰撞）
    manual_run_id = bool(args.run_id)
    if manual_run_id:
        err = validate_run_id(args.run_id)
        if err:
            print(f"错误: {err}", file=sys.stderr)
            sys.exit(2)
        run_id = args.run_id
    else:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out_dir = os.path.join(OUT_BASE, lottery_no)
    os.makedirs(out_dir, exist_ok=True)

    # scope：只有同时满足以下才能标full_pool，否则partial_pool
    #   1) schedule提供
    #   2) 覆盖率=100%（合并场数覆盖schedule全部）
    #   3) schedule本身完整：已知期号要求schedule_total=权威场数；
    #      未知期号要求schedule附带来源证据（source_url+fetched_at）
    # 否则partial_pool，并标denominator/coverage及原因
    coverage_rate = (coverage["coverage_rate"]
                     if coverage and coverage["coverage_rate"] is not None
                     else None)
    scope = "partial_pool"
    scope_reason = None
    if args.schedule and coverage_rate == 1.0 and schedule_evidence:
        se = schedule_evidence
        if se["schedule_complete"]:
            scope = "full_pool"
        elif se["expected_total"] is None:
            # 未知期号：要求来源证据
            if se.get("source_url") and se.get("fetched_at"):
                scope = "full_pool"
                scope_reason = ("未知期号但schedule有来源证据"
                                f"({se['source_url']})，暂标full_pool")
            else:
                scope_reason = "未知期号且schedule无来源证据，只能partial_pool"
        else:
            scope_reason = (f"schedule不完整：schedule_total={se['schedule_total']}，"
                            f"权威场数={se['expected_total']}")
    elif args.schedule and coverage_rate != 1.0:
        scope_reason = f"覆盖率不足100%（{coverage_rate}）"
    elif not args.schedule:
        scope_reason = "未提供schedule"
    denominator = coverage["schedule_total"] if coverage else None
    covered_n = coverage["covered"] if coverage else None

    manifest = {
        "scope": scope,
        "scope_reason": scope_reason,  # partial_pool时注明原因
        "denominator": denominator,   # 赛程总场数；无schedule时为null
        "coverage": covered_n,        # 已覆盖场数；无schedule时为null
        "lottery_no": lottery_no,
        "run_id": run_id,
        "merged_at": datetime.now(timezone.utc).isoformat(),
        "schedule_evidence": schedule_evidence,  # schedule文件SHA256/来源/期号/权威核验
        "chunks": chunk_stats,
        "conflicts": conflicts,
        "pool_total": len(merged),
        "predicted_ok": n_ok,
        "skipped": len(merged) - n_ok,
        "skip_reasons": dict(reasons),
        "coverage_detail": coverage,
        "matches": [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for _, m in sorted(merged.items())
        ],
    }
    payload = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")

    # O_EXCL原子独占写入；同名则报错不覆盖（不用exists+replace，防竞态）
    out_path = os.path.join(out_dir, f"{run_id}.json")
    try:
        atomic_write_excl(out_path, payload)
    except FileExistsError:
        if manual_run_id:
            print(f"错误: manifest已存在 {out_path}，拒绝覆盖。"
                  f"请换run_id后重试。", file=sys.stderr)
            sys.exit(3)
        # 自动run_id极小概率碰撞：重生成一次再试
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") \
            + "_r"
        manifest["run_id"] = run_id
        payload = json.dumps(manifest, ensure_ascii=False,
                             indent=1).encode("utf-8")
        out_path = os.path.join(out_dir, f"{run_id}.json")
        try:
            atomic_write_excl(out_path, payload)
        except FileExistsError:
            print(f"错误: manifest已存在 {out_path}，拒绝覆盖。",
                  file=sys.stderr)
            sys.exit(3)

    print(f"{'全池' if scope == 'full_pool' else '部分'}manifest已写: {out_path}")
    print(f"  scope={scope}, denominator={denominator}, coverage={covered_n}")
    if scope_reason:
        print(f"  scope原因: {scope_reason}")
    if schedule_evidence:
        print(f"  schedule证据: sha256={schedule_evidence['sha256'][:16]}..., "
              f"schedule_total={schedule_evidence['schedule_total']}, "
              f"expected_total={schedule_evidence['expected_total']}, "
              f"complete={schedule_evidence['schedule_complete']}")
    print(f"  合并场数: {len(merged)} (ok={n_ok}, skipped={len(merged)-n_ok})")
    print(f"  冲突: {len(conflicts)}")
    if coverage:
        print(f"  赛程覆盖: {coverage['covered']}/{coverage['schedule_total']} "
              f"({coverage['coverage_rate']:.1%})" if coverage['coverage_rate'] is not None else "")
        if coverage["missing_from_merge"]:
            print(f"  未覆盖场次: {len(coverage['missing_from_merge'])}")
    print("  注意: 本manifest只合并已有chunk清单，不生成新预测；不为凑数赛后重跑。")
    if scope == "partial_pool":
        print("  注意: scope=partial_pool，不可称为全池manifest。")


if __name__ == "__main__":
    main()
