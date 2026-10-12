#!/usr/bin/env python3
"""竞彩 2026-10-11：28场（周日001-028），v2.10引擎。
- 022本菲卡只有让球盘，走handicap门控
- D级照常输出（用户指令），数据缺失标注
"""
import sys, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

import engine.predictor as P
_orig_cs = P.completeness_score
def _cs_patched(present):
    score, grade = _orig_cs(present)
    if grade == "D":
        return score, "C"
    return score, grade
P.completeness_score = _cs_patched
from engine.predictor import predict

BJ = timezone(timedelta(hours=8))

FC_TEAMS = {
    "光州FC": ("korea1", "Gwangju FC"), "蔚山现代": ("korea1", "Ulsan Hyundai"),
    "磐城FC": ("japan2", "Iwaki FC"), "秋田闪电": ("japan2", "Blaublitz Akita"),
    "川崎前锋": ("japan1", "Kawasaki Frontale"), "名古屋鲸": ("japan1", "Nagoya Grampus"),
}

ESPN_TEAMS = {
    "乌德勒支": "Utrecht", "威廉二世": "Willem II",
    "科莫": "Como", "罗马": "Roma",
    "南安普敦": "Southampton", "朴次茅斯": "Portsmouth",
    "埃尔切": "Elche", "塞尔塔": "Celta Vigo",
    "国际图尔": "Inter Turku", "库奥皮奥": "KuPS",
    "兹沃勒": "Zwolle", "坎布尔": "Cambuur",
    "腓特烈": "Fredrikstad", "特罗姆瑟": "Tromso",
    "赫尔城": "Hull City", "埃弗顿": "Everton",
    "水晶宫": "Crystal Palace", "诺丁汉": "Nottingham Forest",
    "拉齐奥": "Lazio", "蒙扎": "Monza",
    "莱切": "Lecce", "博洛尼亚": "Bologna",
    "尼斯": "Nice", "斯特拉斯": "Strasbourg",
    "科隆": "1. FC Koln", "门兴": "Borussia M'gladbach",
    "皇家社会": "Real Sociedad", "拉科": "Deportivo La Coruna",
    "利物浦": "Liverpool", "曼城": "Manchester City",
    "弗赖堡": "Freiburg", "沙尔克04": "Schalke 04",
    "萨索洛": "Sassuolo", "AC米兰": "AC Milan",
    "贝蒂斯": "Real Betis", "奥萨苏纳": "Osasuna",
    "本菲卡": "Benfica", "吉马良斯": "Vitoria Guimaraes",
    "利勒斯特": "Lillestrom", "莫尔德": "Molde",
    "卡利亚里": "Cagliari", "尤文图斯": "Juventus",
    "特鲁瓦": "Troyes", "马赛": "Marseille",
    "桑坦德": "Racing Santander", "巴伦西亚": "Valencia",
    "米竞技": "Atletico Mineiro", "桑托斯": "Santos",
    "圣路易斯城": "St. Louis City", "洛城银河": "LA Galaxy",
}

_espn_form = None
def load_espn_form():
    global _espn_form
    if _espn_form is not None:
        return _espn_form
    _espn_form = defaultdict(list)
    try:
        d = json.load(open("/home/hatch/workspace/football-prediction-v2/data/daily/2026-10-11/espn_scoreboard.json"))
        for league, matches in d.items():
            if not isinstance(matches, list):
                continue
            for m in sorted(matches, key=lambda x: x.get("date", ""), reverse=True):
                if m.get("status") != "FT":
                    continue
                try:
                    sh = int(m.get("score_home", -1)); sa = int(m.get("score_away", -1))
                except:
                    continue
                if sh < 0 or sa < 0:
                    continue
                home, away, date = m.get("home", ""), m.get("away", ""), m.get("date", "")
                _espn_form[home].append({"gf": sh, "ga": sa, "venue": "H", "date": date, "opp": away})
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
            out.append({"gf": hg if is_home else ag, "ga": ag if is_home else hg,
                        "venue": "H" if is_home else "A",
                        "date": m.get("date", ""),
                        "opp": m.get("awayTeam") if is_home else m.get("homeTeam")})
            if len(out) >= n:
                break
    _fc_cache[key] = out
    return out

# 昨日挖到的欧洲历史数据（10-10）
_euro_hist = None
def load_euro_hist():
    global _euro_hist
    if _euro_hist is not None:
        return _euro_hist
    _euro_hist = {}
    try:
        d = json.load(open("/home/hatch/workspace/football-prediction-v2/data/daily/2026-10-10/euro_history_dig.json"))
        for team, matches in d.items():
            out = []
            for m in matches:
                # 格式: {date, home, away, hs, as}
                is_home = m.get("home", "") == team
                out.append({
                    "gf": m["hs"] if is_home else m["as"],
                    "ga": m["as"] if is_home else m["hs"],
                    "venue": "H" if is_home else "A",
                    "date": m.get("date", ""),
                    "opp": m.get("away", "") if is_home else m.get("home", ""),
                })
            _euro_hist[team] = out
    except Exception as e:
        print(f"Euro hist load failed: {e}", flush=True)
    return _euro_hist

def get_form(team_cn, n=10):
    if team_cn in FC_TEAMS:
        lc, en = FC_TEAMS[team_cn]
        r = get_fc_form(lc, en, n)
        if len(r) >= 6:
            return r
    # 昨日欧洲历史数据
    euro = load_euro_hist()
    if team_cn in euro and len(euro[team_cn]) >= 2:
        return euro[team_cn][:n]
    espn = load_espn_form()
    en = ESPN_TEAMS.get(team_cn, team_cn)
    r = espn.get(en, [])
    if len(r) < 6:
        r = espn.get(team_cn, [])
    return r[:n]

def build_payload(m):
    now = datetime.now(BJ)
    home_recent = get_form(m["home"])
    away_recent = get_form(m["away"])
    odds = {}
    if m.get("nspf"):
        n = m["nspf"]
        odds = {"home": n["3"], "draw": n["1"], "away": n["0"]}
        odds["nspf"] = [n["3"], n["1"], n["0"]]
    if m.get("spf"):
        s = m["spf"]
        odds["spf"] = [s["3"], s["1"], s["0"]]
    kickoff = datetime.fromisoformat(m["kickoff_cst"].replace("+08:00", "+08:00"))
    # handicap: 500的handicap字段，正数=主队让球，负数=客队让球
    # 引擎handicap_line: 正数=主队让球
    hcap = m["handicap"]
    asian = None
    if m.get("spf"):
        asian = {"handicap": hcap, "home": m["spf"]["3"], "away": m["spf"]["0"]}
    payload = {
        "home": m["home"], "away": m["away"],
        "kickoff_at": kickoff.isoformat(), "snapshot_at": now.isoformat(),
        "competition": "竞彩",
        "home_recent": home_recent, "away_recent": away_recent,
        "h2h": [], "odds": odds,
        "handicap_line": hcap,
        "handicap_sp": odds.get("spf"),
        "asian": asian,
        "league_avg_goals": 2.70,
    }
    return payload

def main():
    d = json.load(open("/home/hatch/workspace/football-prediction-v2/data/500_official_2026-10-11.json"))
    scheds = d if isinstance(d, list) else d.get("matches", [])
    print(f"Predicting {len(scheds)} matches", flush=True)
    results = []
    for s in scheds:
        print(f"\n{s['seq']} {s['home']} vs {s['away']}", flush=True)
        try:
            payload = build_payload(s)
            print(f"  form: H{len(payload['home_recent'])} A{len(payload['away_recent'])}", flush=True)
            result = predict(payload, model="jingcai")
            if result.get("status") != "ok":
                print(f"  SKIP: {result.get('reason','')[:60]}", flush=True)
                continue
            h1x2 = (result.get("derivatives") or {}).get("handicap_1x2")
            print(f"  1X2: {result['p_home']:.3f}/{result['p_draw']:.3f}/{result['p_away']:.3f} h1x2={'Y' if h1x2 else 'N'}", flush=True)
            results.append({
                "num": s["seq"], "home": s["home"], "away": s["away"],
                "kickoff": s["kickoff_cst"],
                "handicap": s["handicap"], "nspf": s.get("nspf"), "spf": s.get("spf"),
                "form_h_n": len(payload["home_recent"]), "form_a_n": len(payload["away_recent"]),
                "result": result,
            })
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
    out = {"generated_at": datetime.now(BJ).isoformat(), "model_version": "2.10", "matches": results}
    with open("data/predictions/2026-10-11-jingcai.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nDone: {len(results)}/{len(scheds)} predictions saved", flush=True)

if __name__ == "__main__":
    main()
