"""API-Football native-ID history audit, never a Chinese/canonical ID approval."""
import hashlib
import json

from .snapshot import _datetime


def _integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label}: invalid integer")
    return value


def audit_history(raw_bytes, receipt, *, verified_at, league_id, season):
    """Accept one complete FT response; preserve explicit missing halves as null.

    Availability starts at our actual verification, never at an old kickoff.
    Provider-native consistency does not approve a Beidan fixture join.
    """
    now = _datetime(verified_at, "verified_at")
    sha = hashlib.sha256(raw_bytes).hexdigest()
    start = _datetime(receipt["start_utc"], "start_utc")
    end = _datetime(receipt["end_utc"], "end_utc")
    if (not start <= end <= now or receipt.get("http_status") != 200
            or receipt.get("sha256") != sha or receipt.get("size_bytes") != len(raw_bytes)):
        raise ValueError("raw body/receipt/verification clock mismatch")
    p = json.loads(raw_bytes)
    params = {"league": str(league_id), "season": str(season), "status": "FT"}
    if (p.get("get") != "fixtures" or p.get("parameters") != params
            or receipt.get("params") != params or p.get("errors") != []
            or p.get("paging") != {"current": 1, "total": 1}
            or not isinstance(p.get("response"), list)
            or p.get("results") != len(p["response"])):
        raise ValueError("incomplete/incorrect provider response, parameters or pagination")
    records, seen = [], set()
    for e in p["response"]:
        f, l, t, s = e["fixture"], e["league"], e["teams"], e["score"]
        fid = _integer(f.get("id"), "fixture.id", 1)
        hid = _integer(t["home"].get("id"), "home.id", 1)
        aid = _integer(t["away"].get("id"), "away.id", 1)
        if fid in seen or hid == aid:
            raise ValueError("duplicate fixture or identical team IDs")
        seen.add(fid)
        kickoff = _datetime(f["date"], "fixture.date")
        if (kickoff >= start or f.get("timestamp") != int(kickoff.timestamp())
                or f.get("status", {}).get("short") != "FT"
                or l.get("id") != league_id or l.get("season") != season):
            raise ValueError("fixture time, regular FT status or competition mismatch")
        ft = {side: _integer(s["fulltime"].get(side), "fulltime") for side in ("home", "away")}
        if e.get("goals") != ft or any(s.get(k) != {"home": None, "away": None}
                                       for k in ("extratime", "penalty")):
            raise ValueError("fulltime/goals mismatch or non-regular-time result")
        ht = s.get("halftime", {})
        halves = {}
        for side in ("home", "away"):
            value = ht.get(side)
            if value is not None:
                _integer(value, "halftime")
                if value > ft[side]:
                    raise ValueError("halftime exceeds fulltime")
            halves[side] = value
        records.append({"match_id": f"af-{fid}", "provider_match_id": fid,
            "home_id": f"af-team-{hid}", "away_id": f"af-team-{aid}",
            "competition_id": f"af-league-{league_id}-season-{season}",
            "provider_home_id": hid, "provider_away_id": aid,
            "provider_league_id": league_id, "season": season,
            "home": t["home"]["name"], "away": t["away"]["name"],
            "kickoff_at": kickoff.isoformat(), "regular_time": True,
            "ft_home": ft["home"], "ft_away": ft["away"],
            "ht_home": halves["home"], "ht_away": halves["away"],
            "verified_at": verified_at, "result_available_at": None,
            "provider_native_structure_verified": True,
            "canonical_identity_approved": False, "identity_verified": False,
            "human_reviewed": False, "beidan_fixture_binding": None,
            "raw_sha256": sha, "production_eligible": False})
    return records, {"verified_at": verified_at, "raw_sha256": sha,
        "raw_bytes": len(raw_bytes), "records_n": len(records),
        "explicit_halftime_n": sum(r["ht_home"] is not None and r["ht_away"] is not None for r in records),
        "canonical_identity_approved_n": 0, "production_eligible": False}


def predict_native_shadow(raw_bytes, receipt, *, verified_at, asof_at, fixture,
                          handicap=None, ridge=5.0, min_history=30, min_team=5):
    """Fit source-native research prediction without asserting canonical approval.

    `fixture` is an independently archived provider fixture. The caller must
    retain its receipt/hash and Beidan binding separately. No SP is consumed.
    """
    import math
    from .baseline import _history
    from .team_strength import _fit_rows
    asof = _datetime(asof_at, "asof_at")
    kickoff = _datetime(fixture["fixture"]["date"], "fixture.date")
    if not _datetime(verified_at, "verified_at") <= asof < kickoff:
        raise ValueError("history is not available before forecast/kickoff")
    if fixture["fixture"]["status"]["short"] != "NS":
        raise ValueError("future fixture is not unstarted")
    if (fixture["fixture"].get("timestamp") != int(kickoff.timestamp())
            or fixture.get("goals") != {"home": None, "away": None}
            or any(fixture.get("score", {}).get(k) != {"home": None, "away": None}
                   for k in ("halftime", "fulltime", "extratime", "penalty"))):
        raise ValueError("future fixture clock or unknown score fields inconsistent")
    if handicap is not None and (isinstance(handicap, bool) or not isinstance(handicap, int)):
        raise ValueError("official handicap must be integer or null")
    if isinstance(ridge, bool) or not isinstance(ridge, (int, float)) or not math.isfinite(ridge) or ridge <= 0:
        raise ValueError("invalid ridge")
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in (min_history, min_team)):
        raise ValueError("invalid sample thresholds")
    league, teams = fixture["league"], fixture["teams"]
    lid = _integer(league["id"], "league.id", 1)
    season = _integer(league["season"], "season", 1)
    hid = _integer(teams["home"]["id"], "home.id", 1)
    aid = _integer(teams["away"]["id"], "away.id", 1)
    if hid == aid:
        raise ValueError("same team on both sides")
    rows, report = audit_history(raw_bytes, receipt, verified_at=verified_at, league_id=lid, season=season)
    # Missing halves remain archived but cannot enter the six-market chain.
    eligible = [r for r in rows if r["ht_home"] is not None and r["ht_away"] is not None]
    rows = _history(eligible, asof)
    result = _fit_rows(rows, home_id=f"af-team-{hid}", away_id=f"af-team-{aid}",
        competition_id=f"af-league-{lid}-season-{season}", handicap=handicap,
        ridge=ridge, min_history=min_history, min_team=min_team)
    result.update({"route": "L1_provider_native_research", "family_status": "provider_native_only",
        "production_eligible": False, "canonical_identity_approved": False,
        "human_reviewed": False, "beidan_fixture_binding_approved": False,
        "provider_match_id": fixture["fixture"]["id"], "source_audit": report,
        "asof_at": asof_at, "verified_at": verified_at})
    return result
