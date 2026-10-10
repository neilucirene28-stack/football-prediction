#!/usr/bin/env python3
"""竞彩 2026-10-09 预测：2场（周五001韩职、周五002日职）→ v2.9引擎。

数据：500.com官方赛程+SP，footballcharts历史比分（K1/J1），
      ESPN J1近况，The Odds API亚盘/大小球。
输出：data/predictions/2026-10-09-jingcai.json
"""
import sys, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/collector/sources")

from engine.jingcai_predictor import predict

BJ = timezone(timedelta(hours=8))

# 今日赛程（500.com 13:20抓取）
SCHEDULE = [
    {
        "num": "周五001", "league": "韩职", "home": "仁川联", "away": "浦项制铁",
        "kickoff": "2026-10-09T15:30:00+08:00", "rq": -1,
        "nspf": {"3": 2.28, "1": 2.85, "0": 2.94},
        "spf": {"3": 5.85, "1": 3.65, "0": 1.46},
    },
    {
        "num": "周五002", "league": "日职", "home": "柏太阳神", "away": "神户胜利船",
        "kickoff": "2026-10-09T18:00:00+08:00", "rq": -1,
        "nspf": {"3": 2.23, "1": 3.22, "0": 2.70},
        "spf": {"3": 5.10, "1": 3.90, "0": 1.48},
    },
]

# 队名映射（footballcharts英文名）
FC_TEAMS = {
    "仁川联": ("korea1", "Incheon"),
    "浦项制铁": ("korea1", "Pohang"),
    "柏太阳神": ("japan1", "Kashiwa Reysol"),
    "神户胜利船": ("japan1", "Vissel Kobe"),
}


def get_form(league_code, team_en, n=15):
    """从footballcharts拿近N场比分，转home_recent/away_recent格式。"""
    from footballcharts import get_results
    matches = get_results(league_code)
    if not matches:
        return []
    # 按日期倒序，找该队的比赛
    team_matches = []
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
            team_matches.append({
                "gf": hg if is_home else ag,
                "ga": ag if is_home else hg,
                "venue": "H" if is_home else "A",
                "date": m.get("date", ""),
                "opp": m.get("awayTeam") if is_home else m.get("homeTeam"),
            })
            if len(team_matches) >= n:
                break
    return team_matches


def build_payload(sched):
    now = datetime.now(BJ)
    league_code, team_en = FC_TEAMS[sched["home"]]
    _, away_en = FC_TEAMS[sched["away"]]

    home_recent = get_form(league_code, team_en)
    away_recent = get_form(league_code, away_en)

    # H2H
    h2h = []
    try:
        from footballcharts import get_results
        matches = get_results(league_code)
        for m in sorted(matches, key=lambda x: x.get("date", ""), reverse=True):
            ht, at = m.get("homeTeam"), m.get("awayTeam")
            if {ht, at} == {team_en, away_en}:
                score = m.get("score", "")
                if ":" in score:
                    hg, ag = map(int, score.split(":"))
                    # 从主队视角
                    if ht == team_en:
                        h2h.append({"gf": hg, "ga": ag})
                    else:
                        h2h.append({"gf": ag, "ga": hg})
                    if len(h2h) >= 5:
                        break
    except Exception:
        pass

    payload = {
        "home": sched["home"],
        "away": sched["away"],
        "kickoff_at": sched["kickoff"],
        "snapshot_at": now.isoformat(),
        "competition": sched["league"],
        "home_recent": home_recent,
        "away_recent": away_recent,
        "h2h": h2h,
        "odds": {
            "home": sched["nspf"]["3"],
            "draw": sched["nspf"]["1"],
            "away": sched["nspf"]["0"],
        },
        "handicap_line": sched["rq"],
        "handicap_sp": [sched["spf"]["3"], sched["spf"]["1"], sched["spf"]["0"]],
        "league_avg_goals": 2.70,
        "raw": {
            "form_source": "footballcharts",
            "nspf_source": "500.com",
            "date": "2026-10-09",
        },
    }
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
            results.append({
                "no": sched["num"], "status": "insufficient",
                "reason": res.get("reason"), "grade": res.get("grade"),
            })
            print(f"  数据不足跳过: {res.get('reason')}", flush=True)
            continue
        results.append({
            "no": sched["num"],
            "mid": sched["num"],
            "home": sched["home"],
            "away": sched["away"],
            "kickoff_beijing": sched["kickoff"],
            "league": sched["league"],
            "handicap_line": sched["rq"],
            "official_sp": sched["nspf"],
            "official_handicap_sp": sched["spf"],
            "payload": {k: v for k, v in payload.items() if k != "raw"},
            "result": res,
        })
        print(f"  胜平负: {res['p_home']:.1%} / {res['p_draw']:.1%} / {res['p_away']:.1%}", flush=True)

    out = {
        "generated_at": datetime.now(BJ).isoformat(),
        "model_version": "2.9",
        "matches": results,
    }
    path = "/home/hatch/workspace/football-prediction-v2/data/predictions/2026-10-09-jingcai.json"
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"\n已保存: {path}", flush=True)


if __name__ == "__main__":
    main()
