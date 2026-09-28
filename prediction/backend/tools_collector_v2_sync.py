#!/usr/bin/env python3
# collector_v2 -> 数据库 同步程序（幂等）
import os, json, glob, sys, uuid
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.db import get_pool, init_db

COLLECTOR_DATA = os.environ.get("COLLECTOR_DATA", "/opt/football-collector-v2/football-collector-release/data")
SOURCE = "collector_v2"

def latest_run_dir():
    dirs = [d for d in glob.glob(os.path.join(COLLECTOR_DATA, "*")) if os.path.isdir(d)]
    if not dirs:
        return None
    return max(dirs, key=os.path.getmtime)

def resolve_match_uuid(pool, cid):
    with pool.connection() as conn:
        cur = conn.execute("SELECT match_id FROM match_source_ids WHERE source=%s AND external_match_id=%s", (SOURCE, str(cid)))
        r = cur.fetchone()
        return str(r[0]) if r else None

def upsert_europe(pool, mu, items, rid):
    with pool.connection() as conn:
        for it in items:
            bk = it.get("bookmakerName", "")
            cur = conn.execute("SELECT 1 FROM odds_europe WHERE match_id=%s AND bookmaker=%s AND snapshot_id=%s LIMIT 1", (mu, bk, rid))
            if cur.fetchone():
                continue
            conn.execute("INSERT INTO odds_europe (match_id, bookmaker, home_odds, draw_odds, away_odds, company, odds_type, is_opening, observed_at, snapshot_id, raw_payload) VALUES (%s,%s,%s,%s,%s,%s,'europe',%s,now(),%s,%s)", (mu, bk, it.get("firstOdds1"), it.get("firstOdds2"), it.get("firstOdds3"), bk, True, rid, json.dumps(it, ensure_ascii=False)))

def upsert_asia(pool, mu, items, rid):
    with pool.connection() as conn:
        for it in items:
            bk = it.get("bookmakerName", "")
            cur = conn.execute("SELECT 1 FROM odds_asia WHERE match_id=%s AND bookmaker=%s AND snapshot_id=%s LIMIT 1", (mu, bk, rid))
            if cur.fetchone():
                continue
            conn.execute("INSERT INTO odds_asia (match_id, bookmaker, handicap, home_odds, away_odds, company, handicap_text, is_opening, observed_at, snapshot_id, raw_payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s)", (mu, bk, it.get("firstHandicap"), it.get("firstOdds1"), it.get("firstOdds3"), bk, it.get("firstHandicapName"), True, rid, json.dumps(it, ensure_ascii=False)))

def upsert_ou(pool, mu, items, rid):
    with pool.connection() as conn:
        for it in items:
            bk = it.get("bookmakerName", "")
            cur = conn.execute("SELECT 1 FROM odds_over_under WHERE match_id=%s AND bookmaker=%s AND snapshot_id=%s LIMIT 1", (mu, bk, rid))
            if cur.fetchone():
                continue
            conn.execute("INSERT INTO odds_over_under (match_id, bookmaker, line, over_odds, under_odds, company, line_text, is_opening, observed_at, snapshot_id, raw_payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,%s)", (mu, bk, it.get("firstHandicap"), it.get("firstOdds1"), it.get("firstOdds3"), bk, it.get("firstHandicapName"), True, rid, json.dumps(it, ensure_ascii=False)))

def upsert_sporttery(pool, mu, items, rid):
    with pool.connection() as conn:
        for it in items:
            market = it.get("market", "nwdl")
            cur = conn.execute("SELECT 1 FROM odds_sporttery WHERE match_id=%s AND market=%s AND snapshot_id=%s LIMIT 1", (mu, market, rid))
            if cur.fetchone():
                continue
            conn.execute("INSERT INTO odds_sporttery (match_id, market, handicap, home_odds, draw_odds, away_odds, observed_at, source, snapshot_id, raw_payload) VALUES (%s,%s,%s,%s,%s,%s,now(),%s,%s,%s)", (mu, market, it.get("handicap"), it.get("homeOdds"), it.get("drawOdds"), it.get("awayOdds"), "xiaodianhuo", rid, json.dumps(it, ensure_ascii=False)))


def resolve_match_teams(pool, mu):
    with pool.connection() as conn:
        cur = conn.execute("SELECT home_team_id, away_team_id FROM matches WHERE id=%s", (mu,))
        r = cur.fetchone()
        if not r: return None, None
        return str(r[0]), str(r[1])
def upsert_lineups(pool, mu, ld, ht, at, rid):
    with pool.connection() as conn:
        hid = ld.get("hId"); aid = ld.get("aId")
        for tid, st, sb in [(ht, ld.get("homeStarter",[]), ld.get("homeSubstitute",[])), (at, ld.get("awayStarter",[]), ld.get("awaySubstitute",[]))]:
            if not tid: continue
            for p in st:
                if not isinstance(p, dict): continue
                cur = conn.execute("SELECT 1 FROM match_lineups WHERE match_id=%s AND team_id=%s AND player_name=%s AND is_starting=%s AND snapshot_id=%s LIMIT 1", (mu, tid, p.get("playerName",""), True, rid))
                if cur.fetchone(): continue
                conn.execute("INSERT INTO match_lineups (match_id, team_id, formation, is_starting, player_name, player_number, position, snapshot_id, source, starting_lineup, substitutes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (mu, tid, ld.get("homeFormation") if tid==hid else ld.get("awayFormation"), True, p.get("playerName",""), p.get("shirtNum") or p.get("playerNumber"), p.get("position"), rid, "xiaodianhuo", json.dumps(st, ensure_ascii=False), json.dumps(sb, ensure_ascii=False)))
            for p in sb:
                if not isinstance(p, dict): continue
                cur = conn.execute("SELECT 1 FROM match_lineups WHERE match_id=%s AND team_id=%s AND player_name=%s AND is_starting=%s AND snapshot_id=%s LIMIT 1", (mu, tid, p.get("playerName",""), False, rid))
                if cur.fetchone(): continue
                conn.execute("INSERT INTO match_lineups (match_id, team_id, formation, is_starting, player_name, player_number, position, snapshot_id, source, starting_lineup, substitutes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (mu, tid, ld.get("homeFormation") if tid==hid else ld.get("awayFormation"), False, p.get("playerName",""), p.get("shirtNum") or p.get("playerNumber"), p.get("position"), rid, "xiaodianhuo", json.dumps(st, ensure_ascii=False), json.dumps(sb, ensure_ascii=False)))

def upsert_standings(pool, mu, rd, ht, at, rid, season, h_src_id, a_src_id):
    with pool.connection() as conn:
        for key, tid, src_id in [("homeRanking", ht, h_src_id), ("awayRanking", at, a_src_id)]:
            if src_id is None or src_id == "": print("SKIP standings", key, "missing src_id"); continue
            rows = [x for x in (rd.get(key) or []) if isinstance(x,dict) and str(x.get("teamId"))==str(src_id)]
            if len(rows)<1 or len(rows)>1: print("SKIP standings",key,"src_id",src_id,"n",len(rows)); continue
            r = rows[0]
            cur = conn.execute("SELECT 1 FROM team_standings WHERE match_id=%s AND team_id=%s AND snapshot_id=%s LIMIT 1", (mu, tid, rid))
            if cur.fetchone(): continue
            conn.execute("INSERT INTO team_standings (team_id, competition, season, position, played, won, drawn, lost, points, goal_diff, goals_for, goals_against, match_id, snapshot_id, source, payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (tid, r.get("tournamentName"), season, r.get("rankingNum"), r.get("gamesPlayed"), r.get("wins"), r.get("draws"), r.get("losses"), r.get("point"), r.get("goalsScored",0)-r.get("goalsAgainst",0), r.get("goalsScored"), r.get("goalsAgainst"), mu, rid, "xiaodianhuo", json.dumps(r, ensure_ascii=False)))

def ensure_run(pool, summary):
    run_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "collector_v2:" + summary["runId"]))
    counts = summary.get("counts") or {}
    with pool.connection() as conn:
        conn.execute(
            """INSERT INTO collection_runs
               (id, source, run_type, started_at, finished_at, status,
                total_matches, success_matches, partial_matches, failed_matches, items_collected)
               VALUES (%s, %s, 'collector_sync', %s, %s, 'completed', %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (run_uuid, SOURCE, summary["startedAt"], summary["finishedAt"],
             counts.get("eligible", 0), counts.get("success", 0),
             counts.get("partial", 0), counts.get("failed", 0), counts.get("processed", 0)),
        )
    return run_uuid

def save_snapshot(pool, mu, raw, run_uuid, summary):
    source_time = raw.get("finishedAt") or raw.get("startedAt")
    if not source_time or not raw.get("startedAt"):
        raise ValueError("match timestamps missing for " + str(raw.get("matchId")))
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not (parse(summary["startedAt"]) <= parse(raw["startedAt"]) <= parse(source_time) <= parse(summary["finishedAt"])):
        raise ValueError("match timestamps outside run " + summary["runId"])
    payload = json.dumps(raw, ensure_ascii=False)
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, raw_payload FROM match_snapshots WHERE match_id=%s AND collection_run_id=%s AND source=%s",
            (mu, run_uuid, SOURCE),
        ).fetchall()
        if rows:
            if len(rows) != 1 or rows[0][1] != raw:
                raise ValueError("conflicting snapshot in run " + summary["runId"])
            return str(rows[0][0])
        rows = conn.execute(
            "SELECT id FROM match_snapshots WHERE match_id=%s AND source=%s AND collection_run_id IS NULL AND raw_payload=%s::jsonb",
            (mu, SOURCE, payload),
        ).fetchall()
        if len(rows) > 1:
            raise ValueError("ambiguous legacy snapshot for " + str(raw["matchId"]))
        if rows:
            conn.execute(
                "UPDATE match_snapshots SET collection_run_id=%s, collected_at=%s WHERE id=%s",
                (run_uuid, source_time, rows[0][0]),
            )
            return str(rows[0][0])
        row = conn.execute(
            "INSERT INTO match_snapshots (match_id, collection_run_id, collected_at, raw_payload, source) VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (mu, run_uuid, source_time, payload, SOURCE),
        ).fetchone()
        return str(row[0])

def sync_run(run_dir):
    season = os.path.basename(run_dir.rstrip("/"))
    with open(os.path.join(run_dir, "summary.json"), encoding="utf-8") as summary_file:
        summary = json.load(summary_file)
    if summary.get("runId") != season or summary.get("status") != "completed" or not summary.get("finishedAt"):
        print("SKIP run=%s status=%s (not completed)" % (season, summary.get("status")))
        return
    files = sorted(fp for fp in glob.glob(os.path.join(run_dir, "*.json")) if os.path.basename(fp) != "summary.json")
    if len(files) != (summary.get("counts") or {}).get("processed"):
        print("SKIP run=%s file count mismatch: files=%d processed=%s" % (season, len(files), (summary.get("counts") or {}).get("processed")))
        return
    pool = get_pool()
    run_uuid = ensure_run(pool, summary)
    total = 0
    rid = None
    for fp in files:
        if os.path.basename(fp) == "summary.json": continue
        try: d = json.load(open(fp))
        except Exception: continue
        cid = d.get("matchId")
        if not cid: continue
        mu = resolve_match_uuid(pool, cid)
        if not mu:
            print("SKIP no match_uuid for", cid); continue
        rid = save_snapshot(pool, mu, d, run_uuid, summary)
        uo = d.get("unclassifiedOdds", [])
        for o in uo:
            data = o.get("data"); items = []
            if isinstance(data, dict): items = data.get("data", [])
            elif isinstance(data, list): items = data
            for it in items:
                if not isinstance(it, dict): continue
                pt = it.get("playType")
                if pt == 1: upsert_europe(pool, mu, [it], rid)
                elif pt == 2: upsert_asia(pool, mu, [it], rid)
                elif pt == 3: upsert_ou(pool, mu, [it], rid)
        for o in uo:
            if "odds/list/5" in o.get("url", ""):
                dd = o.get("data", {})
                for i, lst in enumerate([dd.get("oddsAiToBaseBoList1"), dd.get("oddsAiToBaseBoList2"), dd.get("oddsAiToBaseBoList3")]):
                    if isinstance(lst, list):
                        for sp in lst:
                            sp["market"] = ["nwdl","handicap","ou"][i] if i < 3 else "nwdl"
                            upsert_sporttery(pool, mu, [sp], rid)
        ht, at = resolve_match_teams(pool, mu)
        ld = d.get("modules", {}).get("lineup", [{}])[0].get("data", {}).get("data", {})
        if ld and ht and at: upsert_lineups(pool, mu, ld, ht, at, rid)
        rd = d.get("modules", {}).get("ranking", [{}])[0].get("data", {}).get("data", {})
        if rd and ht and at: upsert_standings(pool, mu, rd, ht, at, rid, season, ld.get("hId"), ld.get("aId"))
        total += 1
    print("SYNCED run=%s run_uuid=%s matches=%d" % (season, run_uuid, total))

if __name__ == "__main__":
    init_db()
    if len(sys.argv) == 4 and sys.argv[1] == "--after-run":
        cutoff, parent = sys.argv[2], sys.argv[3]
        if not os.path.isdir(parent):
            print("NO RUN DIR", parent); sys.exit(1)
        runs = sorted(d for d in glob.glob(os.path.join(parent, "*"))
                      if os.path.isdir(d) and os.path.basename(d) > cutoff
                      and os.path.isfile(os.path.join(d, "summary.json")))
        if not runs:
            print("NO NEW RUN AFTER", cutoff)
    else:
        arg_dirs = [a for a in sys.argv[1:] if os.path.isdir(a)]
        runs = []
        for a in arg_dirs:
            if os.path.exists(os.path.join(a, "summary.json")):
                runs.append(a)
            else:
                runs.extend(sorted(d for d in glob.glob(os.path.join(a, "*")) if os.path.isdir(d) and os.path.exists(os.path.join(d, "summary.json"))))
        if not runs and len(sys.argv) == 1:
            lrd = latest_run_dir()
            runs = [lrd] if lrd else []
        if not runs: print("NO RUN DIR"); sys.exit(1)
    for rd in runs:
        sync_run(rd)
