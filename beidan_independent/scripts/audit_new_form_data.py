"""Audit Muse's new form export without silently importing it for training."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "data_sample/quarantine_872d0ce"
    records = [json.loads(l) for l in (folder / "beidan_teams_form_20261009.jsonl").read_text().splitlines() if l.strip()]
    events = defaultdict(list)
    for r in records:
        events[(r["provider"], r["provider_match_id"])].append(r)
    conflicts = []
    for key, rows in events.items():
        signatures = {(r["home"], r["away"], r["score_home"], r["score_away"], r["kickoff"], r["league"]) for r in rows}
        if len(signatures) > 1:
            conflicts.append({"provider": key[0], "event_id": key[1], "signatures": sorted(signatures)})
    sport_pattern = re.compile(r"hockey|tennis|basketball|rugby|冰球|网球|篮球", re.I)
    nonfootball = [r for r in records if sport_pattern.search(r["league"])]
    date_only = [r for r in records if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["kickoff"])]
    archives = {}
    for name in ("espn_j1_20261009.json", "espn_eu_20261009.json", "tsdb_low_20261009.json"):
        p = folder / name
        value = json.loads(p.read_text())
        archives[name] = {"sha256_local_export": hashlib.sha256(p.read_bytes()).hexdigest(),
                          "shape": "processed_record_list" if isinstance(value, list) else "processed_team_dictionary",
                          "original_provider_http_body_verified": False}
    output = {"commit": "872d0ce", "checked_at": datetime.now(timezone.utc).isoformat(),
              "record_n": len(records), "unique_provider_event_n": len(events),
              "duplicate_observation_n": sum(len(r)-1 for r in events.values()),
              "provider_counts": dict(Counter(r["provider"] for r in records)),
              "conflicting_events": conflicts, "explicit_nonfootball_records": nonfootball,
              "date_only_n": len(date_only), "all_id_verified_false": all(r["id_verified"] is False for r in records),
              "half_score_missing_n": sum(not all(k in r for k in ("ht_home", "ht_away")) for r in records),
              "raw_archives": archives, "team_record_counts": dict(Counter(r["team_cn"] for r in records)),
              "mapping_review_required": [{"query": "林茨蓝白", "export_name": "LASK", "status": "possible_other_club_not_approved"},
                                          {"query": "奥维也纳青年", "export_name": "Austria Wien Youth", "status": "youth_vs_reserve_identity_unverified"}],
              "model_imported_n": 0, "production_eligible": False,
              "limitations": ["two_external_score_date_spot_checks_do_not_verify_all_records_or_team_mapping",
                              "self_reported_receipt_times_not_original_http_receipts",
                              "processed_copy_is_not_unmodified_provider_response",
                              "no_half_scores_or_regular_time_status_evidence"]}
    dest = root / "outputs/form_audit_872d0ce"
    dest.mkdir(exist_ok=True)
    name = "audit_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    with (dest / name).open("x", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, allow_nan=False)
    print(json.dumps({"path": str(dest/name), "record_n":len(records), "unique_provider_event_n":len(events),
                      "nonfootball_n":len(nonfootball), "date_only_n":len(date_only), "model_imported_n":0},ensure_ascii=False))


if __name__ == "__main__":
    main()
