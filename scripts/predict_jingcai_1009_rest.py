#!/usr/bin/env python3
"""竞彩 2026-10-09 补跑：周五003-012共10场 → v2.9引擎。

数据：500.com官方赛程+SP（13:32抓取），footballcharts历史比分（6联赛），
      ESPN近况（挪超/荷甲/荷乙/英冠4场）。
输出：追加到 data/predictions/2026-10-09-jingcai.json
"""
import sys, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/collector/sources")

from engine.jingcai_predictor import predict

BJ = timezone(timedelta(hours=8))

# 500.com 13:32抓取（含官方让球+SP）
SCHEDULE = [
    {"num": "周五003", "league": "德乙", "home": "海登海姆", "away": "凯泽",
     "kickoff": "2026-10-10T00:30:00+08:00", "rq": -1,
     "nspf": {"3": 1.75, "1": 3.55, "0": 3.62}, "spf": {"3": 3.20, "1": 3.66, "0": 1.84},
     "fc": "germany2", "home_en": "Heidenheim", "away_en": "Kaiserslautern"},
    {"num": "周五004", "league": "瑞超", "home": "哥德堡", "away": "韦斯特罗斯",
     "kickoff": "2026-10-10T01:00:00+08:00", "rq": -1,
     "nspf": {"3": 1.51, "1": 4.00, "0": 4.60}, "spf": {"3": 2.58, "1": 3.55, "0": 2.18},
     "fc": "sweden1", "home_en": "Goteborg", "away_en": "Vasteras SK"},
    {"num": "周五005", "league": "挪超", "home": "布兰", "away": "维京",
     "kickoff": "2026-10-10T01:00:00+08:00", "rq": 1,
     "nspf": {"3": 2.95, "1": 4.00, "0": 1.85}, "spf": {"3": 1.75, "1": 3.88, "0": 3.33},
     "espn_league": "nor.1", "home_espn": "Brann", "away_espn": "Viking"},
    {"num": "周五006", "league": "荷甲", "home": "埃因霍温", "away": "海伦芬",
     "kickoff": "2026-10-10T02:00:00+08:00", "rq": -2,
     "nspf": None, "spf": {"3": 2.09, "1": 4.45, "0": 2.35},
     "espn_league": "ned.1", "home_espn": "PSV Eindhoven", "away_espn": "SC Heerenveen"},
    {"num": "周五007", "league": "荷乙", "home": "赫拉克勒", "away": "瓦尔韦克",
     "kickoff": "2026-10-10T02:00:00+08:00", "rq": -1,
     "nspf": {"3": 1.35, "1": 4.65, "0": 5.75}, "spf": {"3": 2.05, "1": 3.80, "0": 2.64},
     "espn_league": "ned.2", "home_espn": "Heracles Almelo", "away_espn": "RKC Waalwijk"},
    {"num": "周五008", "league": "德甲", "home": "多特蒙德", "away": "不来梅",
     "kickoff": "2026-10-10T02:30:00+08:00", "rq": -1,
     "nspf": {"3": 1.19, "1": 5.70, "0": 8.80}, "spf": {"3": 1.65, "1": 4.15, "0": 3.55},
     "fc": "germany1", "home_en": "Dortmund", "away_en": "Werder Bremen"},
    {"num": "周五009", "league": "法甲", "home": "朗斯", "away": "里昂",
     "kickoff": "2026-10-10T02:45:00+08:00", "rq": -1,
     "nspf": {"3": 2.15, "1": 3.55, "0": 2.61}, "spf": {"3": 4.15, "1": 4.25, "0": 1.53},
     "fc": "france1", "home_en": "Lens", "away_en": "Lyon"},
    {"num": "周五010", "league": "英冠", "home": "西汉姆联", "away": "女王巡游",
     "kickoff": "2026-10-10T03:00:00+08:00", "rq": -1,
     "nspf": {"3": 1.34, "1": 4.55, "0": 6.12}, "spf": {"3": 2.08, "1": 3.60, "0": 2.70},
     "espn_league": "eng.2", "home_espn": "West Ham United", "away_espn": "Queens Park Rangers"},
    {"num": "周五011", "league": "西甲", "home": "马拉加", "away": "西班牙人",
     "kickoff": "2026-10-10T03:00:00+08:00", "rq": 1,
     "nspf": {"3": 2.66, "1": 3.12, "0": 2.31}, "spf": {"3": 1.47, "1": 4.05, "0": 4.95},
     "fc": "spain1", "home_en": "Malaga", "away_en": "Espanyol"},
    {"num": "周五012", "league": "葡超", "home": "布拉加", "away": "里斯本",
     "kickoff": "2026-10-10T03:15:00+08:00", "rq": 1,
     "nspf": {"3": 3.26, "1": 3.20, "0": 1.96}, "spf": {"3": 1.63, "1": 3.65, "0": 4.15},
     "fc": "portugal1", "home_en": "Braga", "away_en": "Sporting CP"},
]


def get_form_fc(league_code, team_en, n=15):
    from footballcharts import get_results
    matches = get_results(league_code)
    if not matches:
        return []
    out = []
    for m in sorted(matches, key=lambda x: x.get("date", ""), reverse=True):
        if m.get("homeTeam") == team_en or m.get("awayTeam") == team_en:
            score = m.get("score", "")
            if ":" not in score:
                continue
            try:
                hg, ag = map(int, score.split(":"))
            except ValueError:
                continue
            is_home = m.get("homeTeam") == team_en
            out.append({
                "gf": hg if is_home else ag, "ga": ag if is_home else hg,
                "venue": "H" if is_home else "A",
                "date": m.get("date", ""),
                "opp": m.get("awayTeam") if is_home else m.get("homeTeam"),
            })
            if len(out) >= n:
                break
    return out


def get_form_espn(league_code, team_name, n=15):
    """ESPN scoreboard按队过滤近况。"""
    from espn import get_scoreboard
    out = []
    # 抓最近几周的scoreboard
    for _ in range(1):
        try:
            sb = get_scoreboard(league_code)
        except Exception:
            return []
        events = sb if isinstance(sb, list) else sb.get("events", [])
        for e in events:
            try:
                comp = e["competitions"][0]
                competitors = comp["competitors"]
                home = next(c for c in competitors if c.get("homeAway") == "home")
                away = next(c for c in competitors if c.get("homeAway") == "away")
                hn = home["team"].get("displayName", "")
                an = away["team"].get("displayName", "")
                if team_name.lower() not in hn.lower() and team_name.lower() not in an.lower():
                    continue
                hs = int(home.get("score", 0)); aws = int(away.get("score", 0))
                is_home = team_name.lower() in hn.lower()
                out.append({
                    "gf": hs if is_home else aws, "ga": aws if is_home else hs,
                    "venue": "H" if is_home else "A",
                    "date": e.get("date", "")[:10],
                    "opp": an if is_home else hn,
                })
            except Exception:
                continue
    return out[:n]


def get_h2h_fc(league_code, team_en, away_en, n=5):
    from footballcharts import get_results
    h2h = []
    try:
        matches = get_results(league_code)
        for m in sorted(matches, key=lambda x: x.get("date", ""), reverse=True):
            ht, at = m.get("homeTeam"), m.get("awayTeam")
            if {ht, at} == {team_en, away_en}:
                score = m.get("score", "")
                if ":" in score:
                    hg, ag = map(int, score.split(":"))
                    if ht == team_en:
                        h2h.append({"gf": hg, "ga": ag})
                    else:
                        h2h.append({"gf": ag, "ga": hg})
                    if len(h2h) >= n:
                        break
    except Exception:
        pass
    return h2h


def build_payload(sched):
    now = datetime.now(BJ)
    if sched.get("fc"):
        home_recent = get_form_fc(sched["fc"], sched["home_en"])
        away_recent = get_form_fc(sched["fc"], sched["away_en"])
        h2h = get_h2h_fc(sched["fc"], sched["home_en"], sched["away_en"])
        form_source = "footballcharts"
    else:
        home_recent = get_form_espn(sched["espn_league"], sched["home_espn"])
        away_recent = get_form_espn(sched["espn_league"], sched["away_espn"])
        h2h = []
        form_source = "espn"

    odds = None
    if sched["nspf"]:
        odds = {"home": float(sched["nspf"]["3"]), "draw": float(sched["nspf"]["1"]),
                "away": float(sched["nspf"]["0"])}

    payload = {
        "home": sched["home"], "away": sched["away"],
        "kickoff_at": sched["kickoff"], "snapshot_at": now.isoformat(),
        "competition": sched["league"],
        "home_recent": home_recent, "away_recent": away_recent, "h2h": h2h,
        "handicap_line": sched["rq"],
        "handicap_sp": [float(sched["spf"]["3"]), float(sched["spf"]["1"]), float(sched["spf"]["0"])],
        "league_avg_goals": 2.70,
        "raw": {"form_source": form_source, "nspf_source": "500.com", "date": "2026-10-09"},
    }
    if odds:
        payload["odds"] = odds
    return payload


def main():
    results = []
    for sched in SCHEDULE:
        print(f"预测 {sched['num']} {sched['home']} vs {sched['away']}...", flush=True)
        payload = build_payload(sched)
        print(f"  近况: 主{len(payload['home_recent'])}场 客{len(payload['away_recent'])}场 H2H:{len(payload['h2h'])}场", flush=True)
        try:
            res = predict(payload, None)
        except Exception as e:
            results.append({"no": sched["num"], "status": "error", "error": str(e)[:200]})
            print(f"  ERROR: {e}", flush=True)
            continue
        if res.get("status") == "insufficient_data":
            results.append({"no": sched["num"], "status": "insufficient",
                            "reason": res.get("reason"), "grade": res.get("grade")})
            print(f"  数据不足跳过: {res.get('reason')}", flush=True)
            continue
        results.append({
            "no": sched["num"], "mid": sched["num"],
            "home": sched["home"], "away": sched["away"],
            "kickoff_beijing": sched["kickoff"], "league": sched["league"],
            "handicap_line": sched["rq"],
            "official_sp": sched["nspf"], "official_handicap_sp": sched["spf"],
            "payload": {k: v for k, v in payload.items() if k != "raw"},
            "result": res,
        })
        print(f"  胜平负: {res['p_home']:.1%} / {res['p_draw']:.1%} / {res['p_away']:.1%}", flush=True)

    # 追加到已有文件（不覆盖001/002）
    path = "/home/hatch/workspace/football-prediction-v2/data/predictions/2026-10-09-jingcai.json"
    existing = json.load(open(path, encoding="utf-8"))
    existing["matches"].extend(results)
    existing["generated_at"] = datetime.now(BJ).isoformat()
    json.dump(existing, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"\n已追加 {len(results)} 场，总计 {len(existing['matches'])} 场: {path}", flush=True)


if __name__ == "__main__":
    main()
