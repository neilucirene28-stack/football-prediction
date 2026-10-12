#!/usr/bin/env python3
"""竞彩 2026-10-12：10场（周一001-010），v2.10引擎。
- 赛程：500.com/jczq/ 官方在售列表（周一001-010），小店火页浏览器不可达（VM Chromium被出口代理拦截、页面JS+加密API curl拿不到），以官方在售页为准
- 让球/SP：500官方（GBK静态页实测解析）
- form：openfootball（意甲/西甲/英超/巴甲）+ ESPN现挖（瑞典超/挪超/法乙/葡超，28天）
- 周一001亚冠两队无form（AF免费档无last/season权限），引擎按D级诚实输出，报告单独列"数据不足"区
- 不打D→C补丁，完整度按引擎原规则
"""
import sys, json, re, unicodedata
from datetime import datetime, timezone, timedelta
from collections import defaultdict
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")

from engine.predictor import predict

def _norm(s):
    # 去变音符号 + 小写，用于 Malmö/Vålerenga 这类队名匹配
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()

BJ = timezone(timedelta(hours=8))
BASE = "/home/hatch/workspace/football-prediction-v2"

# openfootball 英文队名映射（中文 -> (联赛, 英文名)）
OF_TEAMS = {
    "亚特兰大": ("意甲", "Atalanta BC"), "威尼斯": ("意甲", "Venezia FC"),
    "都灵": ("意甲", "Torino FC"), "乌迪内斯": ("意甲", "Udinese Calcio"),
    "莱万特": ("西甲", "Levante UD"), "塞维利亚": ("西甲", "Sevilla FC"),
    "考文垂": ("英超", "Coventry City FC"), "纽卡斯尔": ("英超", "Newcastle United FC"),
    "科里蒂巴": ("巴甲", "Coritiba FBC"), "博塔弗戈": ("巴甲", "Botafogo FR"),
}

# ESPN 现挖队名映射（中文 -> (联赛, 英文名关键字)）
ESPN_TEAMS = {
    "马尔默": ("瑞典超", "Malmo"), "卡尔马": ("瑞典超", "Kalmar"),
    "奥斯KFUM": ("挪超", "KFUM"), "瓦勒伦加": ("挪超", "Valerenga"),
    "第戎": ("法乙", "Dijon"), "梅斯": ("法乙", "Metz"),
    "法马利康": ("葡超", "Famalicao"), "阿尔维卡": ("葡超", "Alverca"),
}

# ESPN 补拉（意甲/西甲/英文/巴甲近40天）队名映射
ESPN_TEAMS2 = {
    "亚特兰大": ("意甲", "Atalanta"), "威尼斯": ("意甲", "Venezia"),
    "都灵": ("意甲", "Torino"), "乌迪内斯": ("意甲", "Udinese"),
    "莱万特": ("西甲", "Levante"), "塞维利亚": ("西甲", "Sevilla"),
    "考文垂": ("英超", "Coventry"), "纽卡斯尔": ("英超", "Newcastle"),
    "科里蒂巴": ("巴西甲", "Coritiba"), "博塔弗戈": ("巴西甲", "Botafogo"),
}

_of_cache = None
def load_openfootball():
    global _of_cache
    if _of_cache is not None:
        return _of_cache
    _of_cache = defaultdict(list)
    d = json.load(open(f"{BASE}/data/daily/2026-10-12/openfootball_results.json"))
    for lg, ms in d["leagues"].items():
        for m in sorted(ms, key=lambda x: x.get("date", ""), reverse=True):
            if not m.get("played"):
                continue
            try:
                sh = int(m["score_home"]); sa = int(m["score_away"])
            except (TypeError, ValueError):
                continue
            home, away, dt = m["home"], m["away"], m.get("date", "")
            _of_cache[(lg, home)].append({"gf": sh, "ga": sa, "venue": "H", "date": dt, "opp": away})
            _of_cache[(lg, away)].append({"gf": sa, "ga": sh, "venue": "A", "date": dt, "opp": home})
    return _of_cache

_espn_cache = None
def load_espn_dig():
    global _espn_cache
    if _espn_cache is not None:
        return _espn_cache
    _espn_cache = defaultdict(list)
    try:
        d = json.load(open(f"{BASE}/data/daily/2026-10-12/espn_form_dig.json"))
    except FileNotFoundError:
        return _espn_cache
    for lg, ms in d.items():
        for m in sorted(ms, key=lambda x: x.get("date", ""), reverse=True):
            try:
                sh = int(m.get("score_home", -1)); sa = int(m.get("score_away", -1))
            except (TypeError, ValueError):
                continue
            if sh < 0 or sa < 0:
                continue
            home, away, dt = m.get("home", ""), m.get("away", ""), m.get("date", "")
            _espn_cache[(lg, home)].append({"gf": sh, "ga": sa, "venue": "H", "date": dt, "opp": away})
            _espn_cache[(lg, away)].append({"gf": sa, "ga": sh, "venue": "A", "date": dt, "opp": home})
    return _espn_cache

_espn2_cache = None
def load_espn_dig2():
    global _espn2_cache
    if _espn2_cache is not None:
        return _espn2_cache
    _espn2_cache = defaultdict(list)
    try:
        d = json.load(open(f"{BASE}/data/daily/2026-10-12/espn_form_dig2.json"))
    except FileNotFoundError:
        return _espn2_cache
    for lg, ms in d.items():
        for m in sorted(ms, key=lambda x: x.get("date", ""), reverse=True):
            try:
                sh = int(m.get("score_home", -1)); sa = int(m.get("score_away", -1))
            except (TypeError, ValueError):
                continue
            if sh < 0 or sa < 0:
                continue
            home, away, dt = m.get("home", ""), m.get("away", ""), m.get("date", "")
            _espn2_cache[(lg, home)].append({"gf": sh, "ga": sa, "venue": "H", "date": dt, "opp": away})
            _espn2_cache[(lg, away)].append({"gf": sa, "ga": sh, "venue": "A", "date": dt, "opp": home})
    return _espn2_cache

def _espn_search(dig, lg, key, n):
    nkey = _norm(key)
    cands = []
    for (l, name), rs in dig.items():
        if l == lg and nkey in _norm(name):
            cands.extend(rs)
    cands.sort(key=lambda x: x.get("date", ""), reverse=True)
    # 去重（同一天同一对阵只留一条）
    seen, uniq = set(), []
    for c in cands:
        k = (c.get("date"), c.get("gf"), c.get("ga"), c.get("opp"))
        if k not in seen:
            seen.add(k); uniq.append(c)
    return uniq[:n]

def get_form(team_cn, n=10):
    if team_cn in OF_TEAMS:
        lg, en = OF_TEAMS[team_cn]
        r = load_openfootball().get((lg, en), [])[:n]
        # openfootball 偏旧（意甲只到9-20），用 ESPN 近40天补足
        if len(r) < 6 and team_cn in ESPN_TEAMS2:
            lg2, key2 = ESPN_TEAMS2[team_cn]
            extra = _espn_search(load_espn_dig2(), lg2, key2, n)
            have = set((x.get("date"), x.get("opp")) for x in r)
            for x in extra:
                if (x.get("date"), x.get("opp")) not in have:
                    r.append(x)
                    if len(r) >= n:
                        break
        if r:
            return r
    if team_cn in ESPN_TEAMS:
        lg, key = ESPN_TEAMS[team_cn]
        cands = _espn_search(load_espn_dig(), lg, key, n)
        if cands:
            return cands
    return []

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
    kickoff = datetime.fromisoformat(m["kickoff_cst"])
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
    d = json.load(open(f"{BASE}/data/500_official_2026-10-12.json"))
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
                print(f"  SKIP: {result.get('reason','')[:80]}", flush=True)
                results.append({
                    "num": s["seq"], "home": s["home"], "away": s["away"],
                    "kickoff": s["kickoff_cst"], "handicap": s["handicap"],
                    "nspf": s.get("nspf"), "spf": s.get("spf"),
                    "form_h_n": len(payload["home_recent"]), "form_a_n": len(payload["away_recent"]),
                    "result": result,
                })
                continue
            h1x2 = (result.get("derivatives") or {}).get("handicap_1x2")
            comp_raw = result.get("completeness")
            comp_grade = comp_raw.get("grade") if isinstance(comp_raw, dict) else None
            if comp_grade is None and isinstance(comp_raw, (int, float)):
                comp_grade = "S" if comp_raw >= 85 else "A" if comp_raw >= 70 else "B" if comp_raw >= 55 else "C" if comp_raw >= 40 else "D"
            print(f"  1X2: {result['p_home']:.3f}/{result['p_draw']:.3f}/{result['p_away']:.3f} "
                  f"h1x2={'Y' if h1x2 else 'N'} completeness={comp_raw}({comp_grade})", flush=True)
            results.append({
                "num": s["seq"], "home": s["home"], "away": s["away"],
                "kickoff": s["kickoff_cst"],
                "handicap": s["handicap"], "handicap_title": s.get("handicap_title"),
                "nspf": s.get("nspf"), "spf": s.get("spf"),
                "form_h_n": len(payload["home_recent"]), "form_a_n": len(payload["away_recent"]),
                "completeness": comp_raw, "completeness_grade": comp_grade,
                "result": result,
            })
        except Exception as e:
            print(f"  ERROR: {e}", flush=True)
    out = {"generated_at": datetime.now(BJ).isoformat(), "model_version": "2.10",
           "matches": results,
           "notes": "赛程=500官方在售页(周一001-010)；小店火页不可达故未交叉核对。周一001亚冠两队无form数据。"}
    with open(f"{BASE}/data/predictions/2026-10-12-jingcai.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nDone: {len(results)}/{len(scheds)} predictions saved", flush=True)

if __name__ == "__main__":
    main()
