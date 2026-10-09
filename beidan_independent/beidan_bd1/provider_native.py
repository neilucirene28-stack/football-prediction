"""Research fit from independently audited ESPN source-native FT/HT records.

No cross-provider team joins, canonical approval, or production promotion.
"""
import math
import re
from .baseline import _history
from .snapshot import _datetime
from .team_strength import _fit_rows


def predict_espn_native(rows, *, asof_at, kickoff_at, home_id, away_id,
                        league_id, season_year, season_type, handicap=None,
                        ridge=5.0, min_history=30, min_team=5):
    asof = _datetime(asof_at, "asof_at")
    if asof >= _datetime(kickoff_at, "kickoff_at"):
        raise ValueError("forecast must precede kickoff")
    if not all(isinstance(v, str) and v.isdigit() for v in (home_id, away_id, league_id)) or home_id == away_id:
        raise ValueError("invalid source-native team/league IDs")
    if handicap is not None and (isinstance(handicap, bool) or not isinstance(handicap, int)):
        raise ValueError("handicap must be integer or null")
    if isinstance(ridge, bool) or not isinstance(ridge, (int,float)) or not math.isfinite(ridge) or ridge <= 0:
        raise ValueError("invalid ridge")
    if any(isinstance(v, bool) or not isinstance(v,int) or v<1 for v in (min_history,min_team,season_year,season_type)):
        raise ValueError("invalid sample/stage parameters")
    eligible = []
    for r in rows:
        if (r.get("provider_league_id") != league_id or r.get("season_year") != season_year
                or r.get("season_type") != season_type):
            continue
        if (not isinstance(r.get("summary_sha256"),str) or not re.fullmatch("[0-9a-f]{64}",r["summary_sha256"])
                or r.get("halftime_source") != "header.competitions[0].competitors[*].linescores"):
            raise ValueError("native fit requires audited raw summary provenance")
        if r.get("ht_home") is None or r.get("ht_away") is None:
            continue
        h,a = r.get("provider_home_id"),r.get("provider_away_id")
        if not all(isinstance(v,str) and v.isdigit() for v in (h,a)) or h==a:
            raise ValueError("invalid native team IDs")
        eligible.append({**r,"home_id":"espn-team-"+h,"away_id":"espn-team-"+a})
    selected = _history(eligible,asof)
    result = _fit_rows(selected,home_id="espn-team-"+home_id,away_id="espn-team-"+away_id,
        competition_id=f"espn-league-{league_id}-season-{season_year}-type-{season_type}",
        handicap=handicap,ridge=ridge,min_history=min_history,min_team=min_team)
    result.update({"route":"L1_provider_native_research","family_status":"provider_native_only",
        "production_eligible":False,"canonical_identity_approved":False,"human_reviewed":False,
        "beidan_fixture_binding_approved":False,"asof_at":asof_at,"kickoff_at":kickoff_at,
        "historical_payload_available_before_original_matches":False})
    return result
