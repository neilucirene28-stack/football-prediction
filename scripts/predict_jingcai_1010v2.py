#!/usr/bin/env python3
"""竞彩 2026-10-10 预测 v2：18场，用ESPN+footballcharts补近况。"""
import sys, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from engine.predictor import predict

BJ = timezone(timedelta(hours=8))

# 中文名 -> ESPN英文名映射
ESPN_TEAMS = {
    "阿森纳": "Arsenal", "利兹联": "Leeds United",
    "伊普斯": "Ipswich Town", "富勒姆": "Fulham",
    "切尔西": "Chelsea", "伯恩茅斯": "Bournemouth",
    "维拉": "Aston Villa", "布伦特": "Brentford",
    "巴列卡诺": "Rayo Vallecano", "毕尔巴鄂": "Athletic Club",
    "阿拉维斯": "Alaves", "马竞": "Atletico Madrid",
    "热那亚": "Genoa", "佛罗伦萨": "Fiorentina",
    "柏林联合": "Union Berlin", "埃沃斯堡": "Elversberg",
    "奥格斯堡": "Augsburg", "拜仁": "Bayern Munich",
    "美因茨": "Mainz", "勒沃库森": "Bayer Leverkusen",
    "帕德博恩": "Paderborn", "斯图加特": "VfB Stuttgart",
    "霍芬海姆": "Hoffenheim", "汉堡": "Hamburger SV",
    "里尔": "Lille", "勒阿弗尔": "Le Havre",
}

# footballcharts映射 (J1/K1/瑞超用不了，用ESPN或其他)
FC_TEAMS = {
    "大阪樱花": ("japan1", "Cerezo Osaka"),
    "横滨水手": ("japan1", "Yokohama F. Marinos"),
    "京都": ("japan1", "Kyoto Sanga"),
    "町田泽维": ("japan1", "FC Machida Zelvia"),
    "首尔FC": ("korea1", "FC Seoul"),
    "济州SK": ("korea1", "Jeju United"),
    "金泉尚武": ("korea1", "Gimcheon Sangmu"),
    "安养FC": ("korea1", "FC Anyang"),
}

# 加载ESPN数据构建球队近况
_espn_form = None
def load_espn_form():
    global _espn_form
    if _espn_form is not None:
        return _espn_form
    _espn_form = defaultdict(list)
    try:
        d = json.load(open("/home/hatch/workspace/football-prediction-v2/data/daily/2026-10-10/espn_scoreboard.json"))
        for league, matches in d.items():
            if not isinstance(matches, list):
                continue
            for m in sorted(matches, key=lambda x: x.get("date", ""), reverse=True):
                if m.get("status") != "FT":
                    continue
                try:
                    sh = int(m.get("score_home", -1))
                    sa = int(m.get("score_away", -1))
                except:
                    continue
                if sh < 0 or sa < 0:
                    continue
                home = m.get("home", "")
                away = m.get("away", "")
                date = m.get("date", "")
                # 为主队记录
                _espn_form[home].append({"gf": sh, "ga": sa, "venue": "H", "date": date, "opp": away})
                # 为客队记录
                _espn_form[away].append({"gf": sa, "ga": sh, "venue": "A", "date": date, "opp": home})
    except Exception as e:
        print(f"ESPN load failed: {e}", flush=True)
    return _espn_form

_fc_cache = {}
def get_fc_form(league_code, team_en, n=10):
    key = (league_code, team_en)
    if key in _fc_cache:
        return _fc_cache[key]
    try:
        sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/collector/sources")
        from footballcharts import get_results
        matches = get_results(league_code)
    except Exception:
        _fc_cache[key] = []
        return []
    if not matches:
        _fc_cache[key] = []
        return []
    out = []
    for m in sorted(matches, key=lambda x: x.get("date", ""), reverse=True):
        if m.get("homeTeam") == team_en or m.get("awayTeam") == team_en:
            score = m.get("score", "")
            if ":" not in score:
                continue
            try:
                hg, ag = map(int, score.split(":"))
            except:
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
    _fc_cache[key] = out
    return out

def get_form(team_cn, n=10):
    # 先试footballcharts
    if team_cn in FC_TEAMS:
        lc, en = FC_TEAMS[team_cn]
        r = get_fc_form(lc, en, n)
        if len(r) >= 6:
            return r
    # 再试ESPN
    espn = load_espn_form()
    en = ESPN_TEAMS.get(team_cn, team_cn)
    r = espn.get(en, [])
    # 也试试直接用中文名
    if len(r) < 6:
        r = espn.get(team_cn, [])
    return r[:n]

def build_payload(sched):
    now = datetime.now(BJ)
    home_recent = get_form(sched["home"])
    away_recent = get_form(sched["away"])
    
    odds = {}
    if sched.get("nspf"):
        n = sched["nspf"]
        odds = {"home": n["3"], "draw": n["1"], "away": n["0"]}
        odds["nspf"] = [n["3"], n["1"], n["0"]]
    if sched.get("spf"):
        s = sched["spf"]
        odds["spf"] = [s["3"], s["1"], s["0"]]
    
    kickoff = datetime.fromisoformat(f"{sched['date']}T{sched['time']}:00+08:00")
    
    # asian字段
    asian = None
    if sched.get("spf"):
        asian = {"handicap": sched["rq"], "home": sched["spf"]["3"], "away": sched["spf"]["0"]}
    
    payload = {
        "home": sched["home"], "away": sched["away"],
        "kickoff_at": kickoff.isoformat(), "snapshot_at": now.isoformat(),
        "competition": sched["league"],
        "home_recent": home_recent, "away_recent": away_recent,
        "h2h": [], "odds": odds,
        "handicap": sched["rq"], "handicap_sp": odds.get("spf"),
        "asian": asian,
        "league_avg_goals": 2.7,
    }
    return payload

def main():
    scheds = json.load(open("/tmp/jc1010_schedule.json", encoding="utf-8"))
    print(f"Predicting {len(scheds)} matches", flush=True)
    results = []
    for s in scheds:
        print(f"\n{s['num']} {s['home']} vs {s['away']}", flush=True)
        try:
            payload = build_payload(s)
            print(f"  form: H{len(payload['home_recent'])} A{len(payload['away_recent'])}", flush=True)
            result = predict(payload, model="jingcai")
            if result.get("status") != "ok":
                print(f"  SKIP: {result.get('reason','')[:50]}", flush=True)
                continue
            results.append({
                "num": s["num"], "league": s["league"],
                "home": s["home"], "away": s["away"],
                "kickoff": f"{s['date']} {s['time']}",
                "handicap": s["rq"], "nspf": s.get("nspf"), "spf": s.get("spf"),
                "result": result,
            })
            print(f"  1X2: {result['p_home']:.3f}/{result['p_draw']:.3f}/{result['p_away']:.3f}", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
    
    out = {"generated_at": datetime.now(BJ).isoformat(), "model_version": "2.9", "matches": results}
    with open("data/predictions/2026-10-10-jingcai.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nDone: {len(results)}/{len(scheds)} predictions saved", flush=True)

if __name__ == "__main__":
    main()
