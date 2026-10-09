"""Independent BD-1 shadow run with a complete offered-pool ledger.

This runner never calls the shared Jingcai predictor.  It records an explicit
failure for every fixture it cannot score and writes one exclusive snapshot
per successful prediction.  A supplied roster is not itself proof of official
full-pool completeness; its origin remains a separate audit requirement.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from .baseline import predict_l3
from .history_import import load_verified_history
from .snapshot import _datetime, build_snapshot, save_snapshot


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _available_history_time(history: list[dict], used_ids: list[str]) -> str:
    lookup = {row["match_id"]: row for row in history}
    stamps = []
    for match_id in used_ids:
        row = lookup[match_id]
        stamps.extend(_datetime(row[key], key) for key in ("result_available_at", "verified_at")
                      if row.get(key) is not None)
    return max(stamps).isoformat()


def _read_fixture_jsonl(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"赛程第{line_no}行JSON无效") from exc
            if not isinstance(row, dict):
                raise ValueError(f"赛程第{line_no}行必须是对象")
            rows.append(row)
    return rows


def run_shadow_pool(*, fixtures: list[dict], history: list[dict],
                    root: str | Path, expected_total: int,
                    period: str, synthetic_at: str | None = None,
                    run_id: str | None = None) -> dict:
    """Generate P0 L3 shadow snapshots and immutable full-input-pool ledger.

    `synthetic_at` exists only for deterministic tests/dry-runs.  Such records
    are marked synthetic and are permanently ineligible for as-of backtesting.
    Real runs obtain UTC time from the runtime, never a fixture file timestamp.
    """
    if not isinstance(period, str) or not _SAFE_ID.fullmatch(period):
        raise ValueError("期号非法")
    if not isinstance(expected_total, int) or isinstance(expected_total, bool) or expected_total < 1:
        raise ValueError("预期场数非法")
    if len(fixtures) != expected_total:
        raise ValueError(f"传入赛程{len(fixtures)}场，不等于预期{expected_total}场")
    ids = [row.get("match_id") for row in fixtures]
    if any(not isinstance(i, str) or not _SAFE_ID.fullmatch(i) for i in ids):
        raise ValueError("赛程含非法赛事ID")
    if len(set(ids)) != len(ids):
        raise ValueError("赛程含重复赛事ID")
    if any(row.get("period") != period for row in fixtures):
        raise ValueError("赛程期号不一致")
    if synthetic_at is None:
        asof = datetime.now(timezone.utc)
    else:
        asof = _datetime(synthetic_at, "synthetic_at")
    selected_run_id = run_id or asof.strftime("%Y%m%dT%H%M%S%fZ")
    if not _SAFE_ID.fullmatch(selected_run_id):
        raise ValueError("run_id非法")
    root = Path(root)
    folder = root / "manifests" / period
    folder.mkdir(parents=True, exist_ok=True)
    # Reserve the run before writing any snapshots.  If a process crashes,
    # the reservation stays as evidence of an incomplete run; retry with a
    # new run_id instead of silently interleaving two attempts.
    reservation = folder / f"{selected_run_id}.reserved"
    reserve_fd = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(reserve_fd, "wb") as output:
        output.write((asof.isoformat() + "\n").encode())
        output.flush()
        os.fsync(output.fileno())
    results = []
    for row in fixtures:
        entry = {"match_id": row["match_id"], "period": period}
        try:
            kickoff = _datetime(row.get("kickoff_at"), "kickoff_at")
            if kickoff <= asof:
                raise ValueError("开球已到或已过，拒绝赛后生成")
            family = row.get("competition_family")
            supplied_sources = row.get("sources") or []
            if not isinstance(supplied_sources, list):
                raise ValueError("sources必须为列表")
            input_sources = row.get("input_sources")
            if input_sources is not None and not isinstance(input_sources, dict):
                raise ValueError("input_sources必须为字典")
            # An untimestamped handicap is unavailable as a pre-match input.
            handicap = row.get("official_handicap")
            if handicap is not None and (input_sources is None or "handicap" not in input_sources):
                handicap = None
            if family is not None and (input_sources is None or "family" not in input_sources):
                family = None
            prediction = predict_l3(
                history, asof_at=asof.isoformat(), kickoff_at=kickoff.isoformat(),
                competition_family=family, handicap=handicap,
            )
            history_source = {"name": "verified_result_export", "source_match_id": None,
                              "available_at": _available_history_time(
                                  history, prediction["training_match_ids"]), "status": "ok"}
            training_lineage = {
                "match_ids": prediction["training_match_ids"],
                "latest_available_at": history_source["available_at"],
            }
            sources = supplied_sources + [history_source]
            mapping = ({**input_sources, "history": "verified_result_export"}
                       if input_sources is not None else None)
            generated = (asof if synthetic_at else datetime.now(timezone.utc))
            snapshot = build_snapshot(
                match_id=row["match_id"], period=period,
                kickoff_at=kickoff.isoformat(), asof_at=asof.isoformat(),
                generated_at=generated.isoformat(),
                model_version=prediction["model_version"],
                vectors=prediction["vectors"], sources=sources,
                input_sources=mapping, competition_family=family,
                lambda_home=None, lambda_away=None, handicap=handicap,
                synthetic_sample=synthetic_at is not None,
                training_lineage=training_lineage,
            )
            saved = save_snapshot(root / "snapshots", snapshot)
            entry.update(status="saved", route=prediction["route"],
                         family_status=prediction["family_status"],
                         training_n=prediction["training_n"],
                         snapshot_path=str(saved.relative_to(root)),
                         observation_only=snapshot["observation_only"],
                         as_of_backtest_eligible=snapshot["as_of_backtest_eligible"])
        except (ValueError, KeyError, TypeError, FileExistsError, OSError) as exc:
            entry.update(status="skipped", reason=f"{type(exc).__name__}: {exc}",
                         observation_only=True, as_of_backtest_eligible=False)
        results.append(entry)
    counts = Counter(x["status"] for x in results)
    manifest = {
        "model": "bd1-l3-shadow", "period": period, "run_id": selected_run_id,
        "asof_at": asof.isoformat(), "synthetic_run": synthetic_at is not None,
        "pool_scope": "supplied_roster_unverified", "offered_n": len(fixtures),
        "saved_n": counts["saved"], "skipped_n": counts["skipped"],
        "eligible_n": sum(x["as_of_backtest_eligible"] for x in results),
        "observation_only_n": sum(x["observation_only"] for x in results),
        "matches": results,
    }
    path = folder / f"{selected_run_id}.json"
    data = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="BD-1 独立北单影子预测")
    ap.add_argument("--history", required=True, help="已核验赛果JSONL")
    ap.add_argument("--fixtures", required=True, help="开售池JSONL，含每场来源证据")
    ap.add_argument("--out", required=True, help="不可覆盖快照及清单目录")
    ap.add_argument("--period", required=True)
    ap.add_argument("--expected-total", type=int, required=True)
    args = ap.parse_args()
    report = run_shadow_pool(
        fixtures=_read_fixture_jsonl(args.fixtures),
        history=load_verified_history(args.history), root=args.out,
        expected_total=args.expected_total, period=args.period,
    )
    print(json.dumps({k: v for k, v in report.items() if k != "matches"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
