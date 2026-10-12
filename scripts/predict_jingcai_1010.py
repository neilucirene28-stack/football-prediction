#!/usr/bin/env python3
"""竞彩 2026-10-10 预测：18场（周六001-018）→ v2.9引擎。

数据：500.com官方赛程+SP（/tmp/jc1010_schedule.json），
      footballcharts历史比分，ESPN近况，The Odds API赔率。
输出：data/predictions/2026-10-10-jingcai.json
"""
import sys, json
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from engine.predictor import predict

BJ = timezone(timedelta(hours=8))

# 队名映射（footballcharts英文名）
FC_TEAMS = {
    # J1
    "大阪樱花": ("japan1", "Cerezo Osaka"),
    "横滨水手": ("japan1", "Yokohama Marinos"),
    "京都": ("japan1", "Kyoto Sanga"),
    "町田泽维": ("japan1", "Machida Zelvia"),
    # K1
    "首尔FC": ("korea1", "FC Seoul"),
    "济州SK": ("korea1", "Jeju SK"),
    "金泉尚武": ("korea1", "Gimcheon Sangmu"),
    "安养FC": ("korea1", "FC Anyang"),
    # 英超
    "阿森纳": ("england1", "Arsenal"),
    "利兹联": ("england1", "Leeds United"),
    "伊普斯": ("england1", "Ipswich Town"),
    "富勒姆": ("england1", "Fulham"),
    "切尔西": ("england1", "Chelsea"),
    "伯恩茅斯": ("england1", "Bournemouth"),
    "维拉": ("england1", "Aston Villa"),
    "布伦特": ("england1", "Brentford"),
    # 西甲
    "巴列卡诺": ("spain1", "Rayo Vallecano"),
    "毕尔巴鄂": ("spain1", "Athletic Club"),
    "阿拉维斯": ("spain1", "Alaves"),
    "马竞": ("spain1", "Atletico Madrid"),
    # 意甲
    "热那亚": ("italy1", "Genoa"),
    "佛罗伦萨": ("italy1", "Fiorentina"),
    # 德甲
    "柏林联合": ("germany1", "Union Berlin"),
    "埃沃斯堡": ("germany1", "Elversberg"),
    "奥格斯堡": ("germany1", "Augsburg"),
    "拜仁": ("germany1", "Bayern Munich"),
    "美因茨": ("germany1", "Mainz 05"),
    "勒沃库森": ("germany1", "Bayer Leverkusen"),
    "帕德博恩": ("germany1", "Paderborn"),
    "斯图加特": ("germany1", "VfB Stuttgart"),
    "霍芬海姆": ("germany1", "Hoffenheim"),
    "汉堡": ("germany1", "Hamburger SV"),
    # 法甲
    "里尔": ("france1", "Lille"),
    "勒阿弗尔": ("france1", "Le Havre"),
    # 瑞超
    "米亚尔比": ("sweden1", "Mjallby"),
    "代格福什": ("sweden1", "Degerfors"),
}

_form_cache = {}

def get_form(league_code, team_en, n=10):
    """从footballcharts拿近N场比分。"""
    key = (league_code, team_en)
    if key in _form_cache:
        return _form_cache[key]
    try:
        sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/collector/sources")
        from footballcharts import get_results
        matches = get_results(league_code)
    except Exception as e:
        print(f"  footballcharts {league_code} failed: {e}", flush=True)
        _form_cache[key] = []
        return []
    if not matches:
        _form_cache[key] = []
        return []
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
    _form_cache[key] = team_matches
    return team_matches


def build_payload(sched):
    now = datetime.now(BJ)
    home_fc = FC_TEAMS.get(sched["home"])
    away_fc = FC_TEAMS.get(sched["away"])
    
    home_recent = []
    away_recent = []
    if home_fc:
        home_recent = get_form(home_fc[0], home_fc[1])
    if away_fc:
        away_recent = get_form(away_fc[0], away_fc[1])
    
    # 构建odds（市场信号）
    odds = {}
    if sched.get("nspf"):
        n = sched["nspf"]
        odds["nspf"] = [n["3"], n["1"], n["0"]]
    if sched.get("spf"):
        s = sched["spf"]
        odds["spf"] = [s["3"], s["1"], s["0"]]
    
    kickoff = datetime.fromisoformat(f"{sched['date']}T{sched['time']}:00+08:00")
    
    payload = {
        "home": sched["home"],
        "away": sched["away"],
        "kickoff_at": kickoff.isoformat(),
        "snapshot_at": now.isoformat(),
        "competition": sched["league"],
        "home_recent": home_recent,
        "away_recent": away_recent,
        "h2h": [],
        "odds": odds,
        "handicap": sched["rq"],
        "handicap_sp": odds.get("spf"),
        "league_avg_goals": 2.7,
    }
    return payload


def main():
    scheds = json.load(open("/tmp/jc1010_schedule.json", encoding="utf-8"))
    print(f"Scheduling {len(scheds)} matches", flush=True)
    
    results = []
    for s in scheds:
        print(f"\n{s['num']} {s['home']} vs {s['away']} ({s['league']})", flush=True)
        try:
            payload = build_payload(s)
            print(f"  form: home {len(payload['home_recent'])}场, away {len(payload['away_recent'])}场", flush=True)
            result = predict(payload, model="jingcai")
            if result.get("status") != "ok":
                print(f"  SKIP: {result.get('status')} - {result.get('reason', '')[:60]}", flush=True)
                continue
            results.append({
                "num": s["num"],
                "league": s["league"],
                "home": s["home"],
                "away": s["away"],
                "kickoff": f"{s['date']} {s['time']}",
                "handicap": s["rq"],
                "nspf": s.get("nspf"),
                "spf": s.get("spf"),
                "result": result,
            })
            # 打印关键结果
            r = result
            print(f"  胜平负: {r['p_home']:.3f}/{r['p_draw']:.3f}/{r['p_away']:.3f}", flush=True)
            h = r.get("derivatives", {}).get("handicap_1x2", {})
            if h:
                print(f"  让球: {h.get('p_home',0):.3f}/{h.get('p_draw',0):.3f}/{h.get('p_away',0):.3f} pick={h.get('pick')}", flush=True)
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
            import traceback
            traceback.print_exc()
    
    out = {
        "generated_at": datetime.now(BJ).isoformat(),
        "model_version": "2.9",
        "matches": results,
    }
    path = "data/predictions/2026-10-10-jingcai.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nSaved {len(results)} predictions to {path}", flush=True)


if __name__ == "__main__":
    main()
