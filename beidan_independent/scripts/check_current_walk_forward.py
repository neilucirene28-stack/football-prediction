"""Reproduce qualification checks without inventing historical source clocks."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from beidan_bd1.baseline import _history
from beidan_bd1.history_import import load_verified_history
from beidan_bd1.walk_forward import run_walk_forward


def main():
    root = Path(__file__).resolve().parents[1]
    history = load_verified_history(root / "data_sample/history.jsonl")
    report = json.loads((root / "outputs/20261010_179_shadow.json").read_text())
    now = datetime.now(timezone.utc).isoformat()
    fixtures = [{"period": r["period"], "match_id": r["match_id"], "sport": "football",
                 "kickoff_at": r["kickoff_at"],
                 "fixture_source": {"name": "archived_wdl_table_23a1520", "status": "unverified",
                                    "available_at": None}} for r in report["predictions"]]
    folds = [{"period": "26103", "cutoff_at": now, "expected_total": 179, "fixtures": fixtures}]
    current = run_walk_forward(history=history, folds=folds, results=[], evaluated_at=now)
    by_period = defaultdict(list)
    for row in history:
        by_period[row["match_id"].split(":")[0]].append(row)
    qualification = []
    for period, rows in sorted(by_period.items()):
        first = min(datetime.fromisoformat(r["kickoff_at"]) for r in rows)
        cutoff = first - timedelta(hours=1)
        qualification.append({"period": period, "result_n": len(rows),
                              "illustrative_cutoff_at": cutoff.isoformat(),
                              "cutoff_is_historical_deployment_claim": False,
                              "available_history_n": len(_history(history, cutoff))})
    output = {"label": "data_qualification_not_effectiveness_backtest", "checked_at": now,
              "history_n": len(history), "historical_periods": qualification,
              "current_walk_forward_research": current, "production_gate_passed": False}
    folder = root / "outputs/walk_forward_qualification"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"qualification_{stamp}.json"
    with path.open("x", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, allow_nan=False)
    # These inputs are explicit observations, NOT fake verified examples.
    example = folder / f"input_{stamp}"
    example.mkdir()
    with (example / "history_normalized.jsonl").open("x", encoding="utf-8") as f:
        for r in history:
            f.write(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n")
    (example / "folds.json").write_text(json.dumps(folds, ensure_ascii=False, allow_nan=False))
    (example / "results_pending.jsonl").write_text("")
    print(json.dumps({"report": str(path), "cli_inputs": str(example),
                      "historical_available_n": [r["available_history_n"] for r in qualification],
                      "current_predicted_n": current["predicted_n"], "brier": current["brier_candidate"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
