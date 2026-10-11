"""Audit Football-Data CSV score observations without inventing identities.

No result publication time, timezone, provider team ID or historic feature
availability is inferred from a match date or a bookmaker column.
"""
from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import re

from .snapshot import _datetime


def _goals(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError("score_not_nonnegative_integer")
    number = int(value)
    if number > 80:
        raise ValueError("score_out_of_range")
    return number


def _result(home, away):
    return "H" if home > away else "A" if away > home else "D"


def audit_csv(raw, *, league_code, season_code, source_url, verified_at):
    now = _datetime(verified_at, "verified_at")
    if not re.fullmatch(r"[A-Z]+[0-9]?", league_code) or not re.fullmatch(r"[0-9]{4}", season_code):
        raise ValueError("league_or_season_code_invalid")
    try:
        decoded = raw.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        decoded = raw.decode("cp1252")
        encoding = "cp1252"
    reader = csv.DictReader(io.StringIO(decoded))
    required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
    if (not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames))
            or not required.issubset(reader.fieldnames)):
        raise ValueError("csv_headers_missing_or_duplicated")
    raw_sha = hashlib.sha256(raw).hexdigest()
    rows, rejected, unlabelled, duplicates = [], [], 0, 0
    seen = {}
    for source_row, row in enumerate(reader, 2):
        if all(not str(v or "").strip() for v in row.values()):
            continue
        try:
            if None in row:
                raise ValueError("csv_extra_columns")
            if row["Div"] != league_code:
                raise ValueError("division_does_not_match_file")
            if not row["HomeTeam"].strip() or not row["AwayTeam"].strip() or row["HomeTeam"] == row["AwayTeam"]:
                raise ValueError("team_names_missing_or_same")
            date = None
            for fmt in ("%d/%m/%Y", "%d/%m/%y"):
                try:
                    date = datetime.strptime(row["Date"], fmt).date()
                    break
                except ValueError:
                    pass
            start_year = 2000 + int(season_code[:2])
            end_year = 2000 + int(season_code[2:])
            if date is None or end_year != start_year + 1 or date.year not in (start_year, end_year):
                raise ValueError("date_or_season_invalid")
            time = row.get("Time", "")
            if time and not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", time):
                raise ValueError("observed_time_invalid")
            if not row["FTHG"] and not row["FTAG"] and not row["FTR"]:
                unlabelled += 1
                continue
            if date > now.date():
                raise ValueError("labelled_future_match_date")
            home, away = _goals(row["FTHG"]), _goals(row["FTAG"])
            if row["FTR"] != _result(home, away):
                raise ValueError("fulltime_result_contradiction")
            half_home, half_away = row.get("HTHG", ""), row.get("HTAG", "")
            if not half_home and not half_away:
                hh = ha = None
                if row.get("HTR"):
                    raise ValueError("halftime_result_without_scores")
            else:
                hh, ha = _goals(half_home), _goals(half_away)
                if hh > home or ha > away:
                    raise ValueError("halftime_exceeds_fulltime")
                if row.get("HTR") and row["HTR"] != _result(hh, ha):
                    raise ValueError("halftime_result_contradiction")
            key = (row["Date"], time, row["HomeTeam"], row["AwayTeam"])
            values = (home, away, hh, ha)
            if key in seen:
                if seen[key] != values:
                    raise ValueError("conflicting_duplicate_observation")
                duplicates += 1
                continue
            seen[key] = values
            rows.append({"observation_id": f"football-data-csv:{raw_sha}:{source_row}",
                "source_row": source_row, "source_url": source_url,
                "raw_sha256": raw_sha, "league_code": league_code, "season_code": season_code,
                "observed_date": date.isoformat(), "observed_time": time or None,
                "observed_home": row["HomeTeam"], "observed_away": row["AwayTeam"],
                "ft_home": home, "ft_away": away, "ht_home": hh, "ht_away": ha,
                "verified_at": verified_at})
        except (ValueError, TypeError, KeyError) as exc:
            rejected.append({"source_row": source_row, "reason": str(exc)})
    # A conflicting duplicate invalidates the entire file, including its first row.
    if any(r["reason"] == "conflicting_duplicate_observation" for r in rejected):
        raise ValueError("conflicting_duplicate_observation")
    return rows, {"raw_sha256": raw_sha, "encoding": encoding,
        "rows_accepted_n": len(rows), "explicit_ht_n": sum(r["ht_home"] is not None for r in rows),
        "unlabelled_n": unlabelled, "exact_duplicate_n": duplicates,
        "rejected": rejected, "rejected_reasons": dict(Counter(r["reason"] for r in rejected)),
        "headers": reader.fieldnames, "usage": "forward_only_score_observations_awaiting_identity",
        "provider_team_ids_available": False, "canonical_identity_approved": False,
        "kickoff_timezone_verified": False, "historical_publication_times_available": False,
        "odds_or_handicap_imported": False, "full_chain_eligible": False}
