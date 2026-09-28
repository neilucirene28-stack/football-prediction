# 外部 AI 比赛数据聚合服务（纯数据，不预测、不加工）
from fastapi import HTTPException
import os
import json
from ..db import get_pool

def _row_to_dict(cur, row):
    if row is None:
        return None
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))

def _iso(ts):
    return ts.isoformat() if ts is not None else None

def _resolve_match_uuid(conn, match_id: str):
    cur = conn.execute(
        "SELECT match_id FROM match_source_ids WHERE external_match_id=%s ORDER BY source DESC LIMIT 1",
        (str(match_id),),
    )
    r = cur.fetchone()
    if r:
        return str(r[0])
    return match_id

def get_ai_match_data(match_id: str) -> dict:
    from ..db import init_db, check_db
    import uuid as _uuid
    try:
        _uuid.UUID(match_id)
        is_uuid = True
    except (ValueError, AttributeError):
        is_uuid = match_id.isdigit()
    if not is_uuid:
        raise HTTPException(status_code=400, detail="invalid match_id format")
    if not check_db():
        try:
            init_db()
        except Exception:
            pass
    pool = get_pool()
    missing = []
    with pool.connection() as conn:
        uuid_id = _resolve_match_uuid(conn, match_id)
        import uuid as _uuid2
        try:
            _uuid2.UUID(uuid_id)
        except (ValueError, AttributeError):
            raise HTTPException(status_code=404, detail="match not found")
        cur = conn.execute(
            """
            SELECT m.id, m.competition, m.season, m.sporttery_no, m.kickoff_at,
                   m.status, m.home_score, m.away_score, m.created_at, m.updated_at,
                   m.home_team_id, m.away_team_id,
                   th.canonical_name, ta.canonical_name
            FROM matches m
            JOIN teams th ON th.id = m.home_team_id
            JOIN teams ta ON ta.id = m.away_team_id
            WHERE m.id = %s
            """,
            (uuid_id,),
        )
        m = cur.fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="match not found")
        match = {
            "match_id": str(m[0]),
            "competition": m[1],
            "season": m[2],
            "sporttery_no": m[3],
            "kickoff_at": _iso(m[4]),
            "status": m[5],
            "home_score": m[6],
            "away_score": m[7],
            "created_at": _iso(m[8]),
            "updated_at": _iso(m[9]),
            "home_team_id": str(m[10]),
            "away_team_id": str(m[11]),
            "home_team_name": m[12],
            "away_team_name": m[13],
        }
        home_id = match["home_team_id"]
        away_id = match["away_team_id"]
        cur = conn.execute(
            "SELECT team_id, source, alias_name FROM team_aliases WHERE team_id IN (%s, %s) ORDER BY team_id, source",
            (home_id, away_id),
        )
        aliases = [{"team_id": str(r[0]), "source": r[1], "alias_name": r[2]} for r in cur.fetchall()]
        if not aliases:
            missing.append("team_aliases")
        cur = conn.execute(
            "SELECT id, team_id, competition, season, position, played, won, drawn, lost, points, goal_diff, goals_for, goals_against, captured_at, source, payload FROM team_standings WHERE team_id IN (%s, %s) ORDER BY team_id, captured_at DESC",
            (home_id, away_id),
        )
        standings = [_row_to_dict(cur, r) for r in cur.fetchall()]
        if not standings:
            missing.append("team_standings")
        cur = conn.execute(
            "SELECT id, match_id, team_id, formation, is_starting, player_name, player_number, position, captured_at, source, lineup_status, announced_at, starting_lineup, substitutes, payload FROM match_lineups WHERE match_id = %s ORDER BY team_id, is_starting DESC, player_number",
            (uuid_id,),
        )
        lineups = [_row_to_dict(cur, r) for r in cur.fetchall()]
        if not lineups:
            missing.append("match_lineups")
        cur = conn.execute(
            "SELECT id, team_id, match_id, result, goals_for, goals_against, played_at, source, form_scope, payload, wins, draws, losses FROM team_recent_form WHERE team_id IN (%s, %s) ORDER BY team_id, played_at DESC",
            (home_id, away_id),
        )
        recent_form = [_row_to_dict(cur, r) for r in cur.fetchall()]
        if not recent_form:
            missing.append("team_recent_form")
        cur = conn.execute(
            "SELECT id, bookmaker, home_odds, draw_odds, away_odds, captured_at, company, odds_type, is_opening, observed_at, changed_at, raw_payload FROM odds_europe WHERE match_id = %s ORDER BY bookmaker, observed_at",
            (uuid_id,),
        )
        odds_europe = [_row_to_dict(cur, r) for r in cur.fetchall()]
        if not odds_europe:
            missing.append("odds_europe")
        cur = conn.execute(
            "SELECT id, bookmaker, handicap, home_odds, away_odds, captured_at, company, handicap_text, home_water, away_water, is_opening, observed_at, changed_at, raw_payload FROM odds_asia WHERE match_id = %s ORDER BY bookmaker, observed_at",
            (uuid_id,),
        )
        odds_asia = [_row_to_dict(cur, r) for r in cur.fetchall()]
        if not odds_asia:
            missing.append("odds_asia")
        cur = conn.execute(
            "SELECT id, bookmaker, line, over_odds, under_odds, captured_at, company, line_text, over_water, under_water, is_opening, observed_at, changed_at, raw_payload FROM odds_over_under WHERE match_id = %s ORDER BY bookmaker, observed_at",
            (uuid_id,),
        )
        odds_over_under = [_row_to_dict(cur, r) for r in cur.fetchall()]
        if not odds_over_under:
            missing.append("odds_over_under")
        cur = conn.execute(
            "SELECT id, match_id, market, home_odds, draw_odds, away_odds, observed_at, source, snapshot_id, raw_payload, created_at FROM odds_sporttery WHERE match_id = %s ORDER BY market, observed_at",
            (uuid_id,),
        )
        odds_sporttery = [_row_to_dict(cur, r) for r in cur.fetchall()]
        if not odds_sporttery:
            missing.append("odds_sporttery")
        cur = conn.execute(
            "SELECT id, match_id, sp_win, sp_draw, sp_lose, let_sp_win, let_sp_draw, let_sp_lose, change_history, analysis_result, created_at FROM beidan_analysis WHERE match_id = %s",
            (uuid_id,),
        )
        beidan_rows = cur.fetchall()
        beidan_analysis = [_row_to_dict(cur, r) for r in beidan_rows] if beidan_rows else None
        if not beidan_rows:
            missing.append("beidan_analysis")
    raw_modules = None
    collection = {"collected_at": None, "run_id": None, "source": "collector"}
    collector_data = os.environ.get("COLLECTOR_DATA", "/opt/football-collector-v2/football-collector-release/data")
    try:
        if os.path.isdir(collector_data):
            runs = sorted([d for d in os.listdir(collector_data) if os.path.isdir(os.path.join(collector_data, d)) and d[:4].isdigit()], reverse=True)
            for run in runs:
                mp = os.path.join(collector_data, run, f"{match_id}.json")
                if os.path.exists(mp):
                    with open(mp, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    raw_modules = raw.get("modules") or raw.get("raw_modules")
                    collection["collected_at"] = raw.get("collectedAt") or raw.get("finishedAt")
                    collection["run_id"] = run
                    break
            if collection["run_id"] is None and runs:
                sp = os.path.join(collector_data, runs[0], "summary.json")
                if os.path.exists(sp):
                    with open(sp, "r", encoding="utf-8") as f:
                        s = json.load(f)
                    collection["run_id"] = s.get("runId")
                    collection["collected_at"] = s.get("finishedAt")
    except Exception:
        pass
    if raw_modules is None:
        try:
            import urllib.request
            api_base = os.environ.get("COLLECTOR_API_BASE", "http://172.17.0.1:3001")
            with urllib.request.urlopen(f"{api_base}/api/runs", timeout=5) as resp:
                runs_list = json.loads(resp.read()).get("runs", [])
            for run in runs_list:
                url = f"{api_base}/api/runs/{run}/matches/{match_id}"
                try:
                    with urllib.request.urlopen(url, timeout=5) as r:
                        raw = json.loads(r.read())
                    raw_modules = raw.get("modules") or raw.get("raw_modules")
                    collection["run_id"] = run
                    collection["collected_at"] = raw.get("collectedAt") or raw.get("finishedAt")
                    break
                except Exception:
                    continue
        except Exception:
            pass
    total_sections = 9
    present = total_sections - len(missing)
    quality = "complete" if not missing else ("partial" if present >= total_sections * 0.6 else "incomplete")
    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        if isinstance(obj, (bytearray, bytes)):
            return obj.decode("utf-8", "replace")
        return obj
    return {
        "metadata": {
            "match_id": match_id,
            "resolved_uuid": match["match_id"],
            "sporttery_no": match["sporttery_no"],
            "query_type": "numeric" if match_id.isdigit() else "uuid",
        },
        "match": _clean(match),
        "aliases": _clean(aliases),
        "standings": _clean(standings),
        "lineups": _clean(lineups),
        "recent_form": _clean(recent_form),
        "odds_europe": _clean(odds_europe),
        "odds_asia": _clean(odds_asia),
        "odds_over_under": _clean(odds_over_under),
        "odds_sporttery": _clean(odds_sporttery),
        "beidan_analysis": _clean(beidan_analysis),
        "raw_modules": _clean(raw_modules),
        "collection": _clean(collection),
        "data_quality": {"level": quality, "present_sections": present, "total_sections": total_sections, "missing": missing},
        "missing": missing,
    }
