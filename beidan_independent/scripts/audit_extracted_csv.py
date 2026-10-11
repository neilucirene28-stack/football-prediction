"""Audit connector-extracted CSV text, retaining extraction and normalization.

The connector can flatten CSV newlines. Restore only unquoted row markers
with an exact division/date prefix, preserving the original extraction file.
Neither the extraction hash nor the normalized hash is an HTTP-body hash.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from beidan_bd1.football_data_csv import audit_csv


def restore_row_boundaries(text, code):
    if "\n" in text:
        return text, {"method": "already_multiline", "boundaries_restored_n": 0}
    if '"' in text:
        raise ValueError("flattened_quoted_csv_not_safely_reconstructible")
    pattern = rf"\s+({re.escape(code)},[0-9]{{2}}/[0-9]{{2}}/[0-9]{{4}},)"
    restored, count = re.subn(pattern, r"\n\1", text)
    if count == 0:
        raise ValueError("no_unambiguous_csv_row_boundaries")
    return restored + "\n", {"method": "unquoted_exact_division_date_row_markers",
                               "boundaries_restored_n": count}


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "data_sample/football_data_csv/extracted_20261009"
    retrieval = json.loads((folder / "RETRIEVAL.json").read_text())
    now = datetime.now(timezone.utc).isoformat()
    records, audits, failures = [], [], []
    for source in retrieval["sources"]:
        extracted = (folder / source["file"]).read_bytes()
        text = extracted.decode("utf-8")
        if len(text) != source["chars"]:
            raise ValueError("saved extraction length differs from connector result")
        code = Path(source["file"]).stem
        try:
            normalized, restoration = restore_row_boundaries(text, code)
            normalized_bytes = normalized.encode("utf-8")
            rows, audit = audit_csv(normalized_bytes, league_code=code, season_code="2526",
                                   source_url=source["url"], verified_at=now)
            normalized_hash = audit.pop("raw_sha256")
            extracted_hash = hashlib.sha256(extracted).hexdigest()
            normalized_file = "normalized/" + source["file"]
            (folder / "normalized").mkdir(exist_ok=True)
            (folder / normalized_file).write_bytes(normalized_bytes)
            for row in rows:
                row["normalized_text_sha256"] = row.pop("raw_sha256")
                row["extracted_text_sha256"] = extracted_hash
                row["source_original_bytes_verified"] = False
            records.extend(rows)
            audit.update(file=source["file"], normalized_file=normalized_file,
                         league_code=code, season_code="2526", source_url=source["url"],
                         normalized_text_sha256=normalized_hash, extracted_text_sha256=extracted_hash,
                         source_original_bytes_verified=False, row_boundary_restoration=restoration)
            audits.append(audit)
            print(json.dumps({"file": source["file"], "accepted_n": len(rows),
                              "explicit_ht_n": audit["explicit_ht_n"],
                              "rejected": audit["rejected_reasons"]}), flush=True)
        except ValueError as exc:
            failures.append({"file": source["file"], "reason": str(exc)})
    with (folder / "score_observations.jsonl").open("w") as out:
        for row in records:
            out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    report = {"verified_at": now, "retrieved_at": retrieval["retrieved_and_verified_at"],
        "retrieval_method": retrieval["method"], "files_audited_n": len(audits),
        "score_observations_n": len(records), "explicit_ht_n": sum(a["explicit_ht_n"] for a in audits),
        "rejected_n": sum(len(a["rejected"]) for a in audits), "audits": audits,
        "failed_sources": retrieval["failed_results"], "failed_audits": failures,
        "native_l1_imported_n": 0, "production_eligible": False,
        "source_original_bytes_verified": False,
        "usage": "forward_only_score_observations_awaiting_identity",
        "historical_backtest_eligible": False,
        "direct_download_failure": "Direct HTTP retrieval blocked by network policy; no original HTTP-byte capture claimed"}
    (folder / "AUDIT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "audits"}))


if __name__ == "__main__":
    main()
