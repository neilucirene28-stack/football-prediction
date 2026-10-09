#!/usr/bin/env python3
"""
每日数据源自动抓取
- API-Football: 当日赛程+比分 (100次/天额度，约用5-10次)
- football-data.org: 当日赛程 (10次/分钟，约用2次)
- The Odds API: 实时赔率快照 (500次/月，约用10-15次)
- football-charts: 历史比分增量 (5000次/天，约用10次)
- AF predictions: 第三方独立模型三向概率（theopenmodel替代，2026-10-08起）
- ESPN 隐藏 API: 实时比分/赛程 (免费无限制)
- Matchbook: 交易量快照 (volume_weight 影子特征，只记录不进生产)
- Transfermarkt: 伤停名单(每日) + 俱乐部身价(每周一)
- BetExplorer: 降赔榜 + 主要联赛当前赔率 (drift 信号)
- FootyStats: 球队 xG (总进球/上下单双独立信号)
- 澳客北单: 六玩法即时SP + 500彩票交叉验证 (2026-10-06 接入)
- Understat: xG数据 (每周一次，本脚本跳过，由weekly任务处理)

输出: ~/workspace/football-prediction-v2/data/daily/YYYY-MM-DD/
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.expanduser("~/workspace/football-prediction-v2")
SRC_DIR = os.path.join(BASE_DIR, "collector", "sources")
DATA_DIR = os.path.join(BASE_DIR, "data", "daily")
sys.path.insert(0, SRC_DIR)

# 清理 no_proxy 里的 IPv6 条目（httpx 会因此报 InvalidURL）
from _http import clean_no_proxy
clean_no_proxy()

os.makedirs(DATA_DIR, exist_ok=True)

today = datetime.now().strftime("%Y-%m-%d")
tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
day_dir = os.path.join(DATA_DIR, today)
os.makedirs(day_dir, exist_ok=True)

results = {"date": today, "sources": {}}

def save(name, data):
    path = os.path.join(day_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path

# 1. API-Football: 今日+明日赛程
try:
    from apifootball import get_fixtures, get_status
    fixtures_today = get_fixtures(today)
    fixtures_tmr = get_fixtures(tomorrow)
    save("af_fixtures_today", fixtures_today)
    save("af_fixtures_tomorrow", fixtures_tmr)
    st = get_status()
    results["sources"]["api_football"] = {
        "ok": True,
        "today_count": len(fixtures_today),
        "tomorrow_count": len(fixtures_tmr),
        "quota_used": st["requests_used"],
    }
    print(f"[OK] API-Football: 今日{len(fixtures_today)}场 明日{len(fixtures_tmr)}场")
except Exception as e:
    results["sources"]["api_football"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] API-Football: {e}")

# 2. football-data.org: 今日+明日赛程
try:
    from footballdata import get_matches
    matches = get_matches(today, tomorrow)
    save("fd_matches", matches)
    results["sources"]["football_data_org"] = {"ok": True, "count": len(matches)}
    print(f"[OK] football-data.org: {len(matches)}场")
except Exception as e:
    results["sources"]["football_data_org"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] football-data.org: {e}")

# 3. The Odds API: 五大联赛实时赔率
try:
    from oddsapi import get_odds, get_quota, SOCCER_LEAGUES
    odds_data = {}
    for cn_name, sport_key in [("英超", "soccer_epl"), ("西甲", "soccer_spain_la_liga"),
                               ("意甲", "soccer_italy_serie_a"), ("德甲", "soccer_germany_bundesliga"),
                               ("法甲", "soccer_france_ligue_one")]:
        try:
            odds_data[cn_name] = get_odds(sport_key, markets="h2h", regions="eu")
            print(f"  - {cn_name}: {len(odds_data[cn_name])}场有赔率")
        except Exception as e:
            print(f"  - {cn_name} 失败: {e}")
    save("odds_snapshot", odds_data)
    q = get_quota()
    total_matches = sum(len(v) for v in odds_data.values())
    results["sources"]["the_odds_api"] = {
        "ok": True, "matches": total_matches,
        "quota_used": q["used"], "quota_remaining": q["remaining"],
    }
    print(f"[OK] The Odds API: 共{total_matches}场有赔率，本月已用{q['used']}剩{q['remaining']}")
except Exception as e:
    results["sources"]["the_odds_api"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] The Odds API: {e}")

# 4. AF predictions: 第三方独立模型信号（theopenmodel替代，2026-10-08起）
#    theopenmodel 已死（2026-09-16后停更）且其输出从无下游消费，本节替换之。
#    对当日竞彩在售场次批量拉取 AF 第三方模型三向概率。
#    配额守卫：与 fixtures 共享100次/天，fixtures优先；本节每日上限40次，超限跳过。
#    在售判定：board 中文联赛名→AF英文名映射 + 开球时间双重对齐（中英文队名不硬匹配）。
#    时区：board 时间为北京时间，转 UTC 后对 AF（AF 用 UTC）；board 的"今日"横跨
#    UTC 两天，故用 af_fixtures_today + af_fixtures_tomorrow 合并匹配。
#    覆盖缺口：AF 免费档部分联赛/场次无数据（如巴西甲一线队），无数据则跳过记 missed。
#    注意：意甲/巴西甲在AF都叫"Serie A"，靠开球时间区分（时区不同极少撞车）。
#    board 队名（okooo中文名）存入 board_home/board_away，供 predict 脚本按中文名查；
#    同联赛同时开球的多场（如巴甲三场06:30）无法无歧义对齐时不存中文名、predict侧跳过，
#    宁可漏标不 HardCode 错配（错配会产生假分歧信号）。
try:
    from datetime import timezone as _tz, timedelta as _td
    from collections import defaultdict as _dd
    from apifootball import get_status as af_get_status
    from apifootball_predictions import (
        get_predictions as af_get_predictions, BOARD_LEAGUE_TO_AF,
    )
    from okooo import _get_html as okooo_get_html, BASE_URL as OKOOO_BASE
    import re as _re

    _board_html = okooo_get_html(f"{OKOOO_BASE}/jingcai/")
    _bj = _tz(_td(hours=8))
    _slot_teams = _dd(list)  # (af_league, utc_kickoff) -> [(home_zh, away_zh)]
    _unmapped_leagues = set()
    _seen_mid = set()
    for _mm in _re.finditer(r'data-mid="(\d+)"', _board_html):
        _mid = _mm.group(1)
        if _mid in _seen_mid:
            continue
        _seen_mid.add(_mid)
        _seg = _board_html[_mm.start():_mm.start() + 6000]
        _lg = _re.search(r'class="saiming[^"]*"[^>]*?title="([^"]+)"', _seg)
        _ko = _re.search(r"比赛时间:(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", _seg)
        _teams = _re.findall(r'class="zhum[^"]*" title="([^"]+)"', _seg)
        if not (_lg and _ko and len(_teams) >= 2):
            continue
        _af_lg = BOARD_LEAGUE_TO_AF.get(_lg.group(1))
        if _af_lg is None:
            _unmapped_leagues.add(_lg.group(1))
            continue
        _dt_utc = datetime.strptime(_ko.group(1), "%Y-%m-%d %H:%M").replace(
            tzinfo=_bj).astimezone(_tz.utc)
        _slot_teams[(_af_lg, _dt_utc.strftime("%Y-%m-%d %H:%M"))].append(
            (_teams[0], _teams[1]))
    _board_utc = set(_slot_teams.keys())

    _af_all = []
    for _fn in ("af_fixtures_today.json", "af_fixtures_tomorrow.json"):
        _fp = os.path.join(day_dir, _fn)
        if os.path.exists(_fp):
            _af_all.extend(json.load(open(_fp)))

    _st = af_get_status()
    _used = _st.get("requests_used", 0) or 0
    _budget = min(40, max(0, 100 - _used - 5))  # 留5次余量
    _seen_fid, _targets = set(), []
    for fx in _af_all:
        _ko = (fx.get("date") or "")[:16].replace("T", " ")
        _fid = fx.get("fixture_id")
        if not _fid or _fid in _seen_fid:
            continue
        if (fx.get("league", ""), _ko) in _board_utc:
            _seen_fid.add(_fid)
            _targets.append(fx)
        if len(_targets) >= _budget:
            break

    afb_data, afb_miss, afb_ambiguous = {}, [], 0
    for i, fx in enumerate(_targets):
        if i > 0:
            time.sleep(7)  # AF免费档限10次/分钟：节流防rateLimit（2026-10-09首跑踩坑）
        fid = fx["fixture_id"]
        _ko = (fx.get("date") or "")[:16].replace("T", " ")
        _slot_key = (fx.get("league", ""), _ko)
        pr = af_get_predictions(fid)  # 内部已吞异常，失败返回None
        if pr:
            _entry = {
                "kickoff": fx.get("date"),
                "league": fx.get("league"),
                "home": fx.get("home"),
                "away": fx.get("away"),
                "p_home": pr["p_home"],
                "p_draw": pr["p_draw"],
                "p_away": pr["p_away"],
                "advice": pr.get("advice"),
                "source": "af_predictions",
            }
            # 无歧义场次才存 board 中文名（同联赛同时开球的多场跳过，防错配）
            _cands = _slot_teams.get(_slot_key, [])
            if len(_cands) == 1:
                _entry["board_home"], _entry["board_away"] = _cands[0]
            else:
                afb_ambiguous += 1
            afb_data[str(fid)] = _entry
        else:
            afb_miss.append(fid)
    save("afb_predictions", {"matches": afb_data, "missed": afb_miss,
                             "quota_budget": _budget,
                             "ambiguous_skipped": afb_ambiguous,
                             "unmapped_leagues": sorted(_unmapped_leagues)})
    _st2 = af_get_status()
    results["sources"]["af_predictions"] = {
        "ok": True, "matches": len(afb_data), "missed": len(afb_miss),
        "quota_used_after": _st2.get("requests_used"),
    }
    print(f"[OK] AF predictions: {len(afb_data)}场第三方模型信号 "
          f"(在售对齐{len(_targets)}场，预算{_budget})")
    if _unmapped_leagues:
        print(f"  [WARN] 未映射联赛: {sorted(_unmapped_leagues)}")
except Exception as e:
    results["sources"]["af_predictions"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] AF predictions: {e}")

# 5. football-charts: 历史比分增量 (J2/K1/K2)
#    赛季探测：先试当年，不存在(unknown_season)则用 API 返回的可用赛季回退；
#    无可用赛季记为 missing，绝不能把 unknown_season 记成成功。
try:
    from footballcharts import (
        get_results, results_to_history,
        parse_held_seasons, is_unknown_season_error,
    )
    cur_year = datetime.now().year

    def _fetch_fc_with_season_probe(code):
        """返回 (season_used, history)；无可用赛季时抛异常"""
        candidates = [str(cur_year), str(cur_year - 1)]
        tried = set()
        note = ""
        i = 0
        while i < len(candidates):
            season = candidates[i]
            i += 1
            if season in tried:
                continue
            tried.add(season)
            try:
                matches = get_results(code, season)
            except Exception as e:
                msg = str(e)
                if is_unknown_season_error(msg):
                    # API 会告诉实际持有的赛季，加入候选
                    for hs in parse_held_seasons(msg):
                        if hs not in tried and hs not in candidates:
                            candidates.append(hs)
                    note = f"{season}: unknown_season"
                else:
                    note = f"{season}: {msg[:120]}"
                continue
            if not matches:
                note = f"{season}: 返回空"
                continue
            return season, results_to_history(matches)
        raise RuntimeError(f"{code} 无可用赛季 (已试 {sorted(tried)}): {note}")

    fc_report = {}
    fc_all_ok = True
    for league, code in [("J2", "japan2"), ("K1", "korea1"), ("K2", "korea2")]:
        try:
            season_used, history = _fetch_fc_with_season_probe(code)
            entry = {"ok": True, "season": season_used, "count": len(history)}
            if season_used != str(cur_year):
                entry["note"] = f"{cur_year}赛季不存在，已回退到{season_used}赛季"
            fc_report[league] = entry
            print(f"  - {league}: {season_used}赛季 {len(history)}场")
        except Exception as e:
            fc_report[league] = {"ok": False, "missing": True, "error": str(e)[:200]}
            fc_all_ok = False
            print(f"  - {league} 缺失: {e}")
    save("fc_history_increment", fc_report)
    results["sources"]["football_charts"] = {
        "ok": fc_all_ok, "leagues": fc_report,
    }
    if not fc_all_ok:
        results["sources"]["football_charts"]["partial"] = True
    print(f"[{'OK' if fc_all_ok else 'WARN'}] football-charts: {fc_report}")
except Exception as e:
    results["sources"]["football_charts"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] football-charts: {e}")

# 6. ESPN 隐藏 API: 实时比分/赛程 (API-Football备用源，免费无限制)
try:
    from espn import get_scoreboard, LEAGUE_CODES as ESPN_LEAGUES
    espn_data = {}
    for cn_name in ["英超", "西甲", "意甲", "德甲", "法甲", "J1", "巴西甲", "美职"]:
        try:
            sb = get_scoreboard(cn_name)
            espn_data[cn_name] = sb
        except Exception as e:
            print(f"  - ESPN {cn_name} 失败: {e}")
    save("espn_scoreboard", espn_data)
    total = sum(len(v) for v in espn_data.values())
    results["sources"]["espn"] = {"ok": True, "matches": total}
    print(f"[OK] ESPN: 共{total}场")
except Exception as e:
    results["sources"]["espn"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] ESPN: {e}")

# 7. Matchbook: 交易量快照 (volume_weight 影子特征，只记录不进生产)
try:
    from matchbook import get_upcoming_volumes, volume_weight
    mb_events = get_upcoming_volumes()
    vols = sorted([e["volume"] for e in mb_events if e["volume"] > 0],
                  reverse=True)
    med_vol = vols[len(vols) // 2] if vols else 0
    mb_rows = []
    for e in mb_events:
        mb_rows.append({
            "home": e["home"], "away": e["away"],
            "start": e["start"].isoformat() if e["start"] else None,
            "volume": e["volume"],
            # 影子特征：交易量权重，仅记录；生产融合前需 walk-forward 验证
            "volume_weight": volume_weight(e["volume"], med_vol),
        })
    save("matchbook_volume", mb_rows)
    results["sources"]["matchbook"] = {
        "ok": True, "events": len(mb_rows),
        "median_volume": med_vol,
        "note": "volume_weight 为影子特征，不进生产权重/融合/门控",
    }
    print(f"[OK] Matchbook: {len(mb_rows)}场未开赛，交易量中位数{med_vol:.0f}")
except Exception as e:
    results["sources"]["matchbook"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] Matchbook: {e}")

# 7a. Smarkets: 第二交易所 1X2 买卖中点（独立市场信号；无成交量字段，不做 volume）
try:
    from smarkets import get_upcoming_quotes
    sm_rows, sm_skipped = get_upcoming_quotes(hours_ahead=48, max_events=60)
    sm_out = []
    for r in sm_rows:
        sm_out.append({
            "home": r["home"], "away": r["away"],
            "start": r["start"].isoformat() if r["start"] else None,
            "mid_1": r["mid_1"], "mid_x": r["mid_x"], "mid_2": r["mid_2"],
            "spread_1": r["spread_1"], "spread_x": r["spread_x"],
            "spread_2": r["spread_2"],
            "market_id": r["market_id"], "event_id": r["event_id"],
        })
    save("smarkets_quotes", sm_out)
    results["sources"]["smarkets"] = {
        "ok": True, "events": len(sm_out), "skipped": sm_skipped,
        "note": "交易所买卖中点赔率；Smarkets 无 volume 字段，不做成交量源",
    }
    print(f"[OK] Smarkets: {len(sm_out)}场有1X2中点，跳过{sm_skipped}场")
except Exception as e:
    results["sources"]["smarkets"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] Smarkets: {e}")

# 7b. 扩联赛名单：五大联赛 + 北单常客（transfermarkt/betexplorer/footystats 三源共用）
EXPANDED_LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲",
                    "英冠", "西乙", "德乙", "法乙", "荷甲", "葡超",
                    "巴西甲", "美职", "J1联赛", "K1联赛"]

# 8. Transfermarkt: 伤停名单（每日）+ 俱乐部身价（每周一）
try:
    from transfermarkt import get_injuries, get_club_values
    injuries, tm_inj_lg = [], {}
    for lg in EXPANDED_LEAGUES:
        try:
            rows = get_injuries(lg)
            injuries.extend(rows)
            tm_inj_lg[lg] = len(rows)
        except Exception as e:
            tm_inj_lg[lg] = f"FAIL: {str(e)[:80]}"
            print(f"  - Transfermarkt {lg} 伤停失败: {e}")
    save("tm_injuries", injuries)
    tm_entry = {"ok": True, "injuries": len(injuries), "by_league": tm_inj_lg}
    # 身价低频：每周一抓一次即可
    weekday = datetime.now().weekday()
    if weekday == 0:
        values, tm_val_lg = [], {}
        for lg in EXPANDED_LEAGUES:
            try:
                rows = get_club_values(lg)
                values.extend(rows)
                tm_val_lg[lg] = len(rows)
            except Exception as e:
                tm_val_lg[lg] = f"FAIL: {str(e)[:80]}"
                print(f"  - Transfermarkt {lg} 身价失败: {e}")
        save("tm_club_values", values)
        tm_entry["club_values"] = len(values)
        tm_entry["values_by_league"] = tm_val_lg
        print(f"  - 俱乐部身价: {len(values)}队 (每周一更新)")
    else:
        tm_entry["club_values"] = "skipped (weekly, Monday only)"
    results["sources"]["transfermarkt"] = tm_entry
    print(f"[OK] Transfermarkt: 伤停{len(injuries)}人 ({len(EXPANDED_LEAGUES)}联赛)")
except Exception as e:
    results["sources"]["transfermarkt"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] Transfermarkt: {e}")

# 9. BetExplorer: 降赔榜 + 主要联赛当前赔率 (drift 信号，配 Titan007 初盘)
try:
    from betexplorer import get_dropping_odds, get_league_odds
    dropping = get_dropping_odds()
    save("be_dropping_odds", dropping)
    be_odds = {}
    for lg in EXPANDED_LEAGUES:
        try:
            be_odds[lg] = get_league_odds(lg)
        except Exception as e:
            print(f"  - BetExplorer {lg} 失败: {e}")
    save("be_league_odds", be_odds)
    n_odds = sum(len(v) for v in be_odds.values())
    results["sources"]["betexplorer"] = {
        "ok": True, "dropping": len(dropping), "league_rows": n_odds,
    }
    print(f"[OK] BetExplorer: 降赔榜{len(dropping)}场，联赛赔率{n_odds}行")
except Exception as e:
    results["sources"]["betexplorer"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] BetExplorer: {e}")

# 10. FootyStats: 球队 xG (总进球/上下单双的独立信号)
try:
    from footystats import get_team_xg
    fs_xg = {}
    for lg in EXPANDED_LEAGUES:
        try:
            fs_xg[lg] = get_team_xg(lg)
        except Exception as e:
            print(f"  - FootyStats {lg} 失败: {e}")
    save("fs_team_xg", fs_xg)
    n_teams = sum(len(v) for v in fs_xg.values())
    results["sources"]["footystats"] = {"ok": True, "teams": n_teams}
    print(f"[OK] FootyStats: {n_teams}队xG")
except Exception as e:
    results["sources"]["footystats"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] FootyStats: {e}")

# 11. 澳客: 战绩/H2H/即时欧指+亚盘（静态 history 页；大小球/初盘/阵容伤停静态无）
try:
    from okooo import get_board_map, get_match_history
    ok_map = get_board_map("jingcai")
    ok_data = {}
    for (home, away), mid in ok_map.items():
        try:
            ok_data[f"{home}vs{away}"] = {"okooo_mid": mid,
                                          **get_match_history(mid)}
        except Exception as e:
            print(f"  - 澳客 {home}vs{away} 失败: {e}")
    save("okooo_history", ok_data)
    results["sources"]["okooo"] = {"ok": True, "matches": len(ok_data)}
    print(f"[OK] 澳客: {len(ok_data)}场战绩/H2H/即时指数")
except Exception as e:
    results["sources"]["okooo"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] 澳客: {e}")

# 12. 7M体育: 天气/开球时间/亚盘/阵容/战绩/H2H（静态 JS，无反爬）
try:
    from qim import find_mid as qim_find_mid, get_match_snapshot, get_h2h, get_form
    from okooo import get_board_map as _ok_board_map
    qim_map = _ok_board_map("jingcai")
    qim_data, qim_miss = {}, []
    for (home, away) in qim_map.keys():
        try:
            mid = qim_find_mid(home, away)
            if not mid:
                qim_miss.append(f"{home}vs{away}")
                continue
            snap = get_match_snapshot(mid)
            # 战绩/H2H 为增量信息，失败不影响快照主体
            try:
                snap["h2h"] = get_h2h(mid)
            except Exception as e:
                snap["h2h_error"] = str(e)[:120]
            try:
                snap["form"] = get_form(mid)
            except Exception as e:
                snap["form_error"] = str(e)[:120]
            qim_data[f"{home}vs{away}"] = snap
        except Exception as e:
            print(f"  - 7M {home}vs{away} 失败: {e}")
            qim_miss.append(f"{home}vs{away}")
    save("qim_match_data", {"source": "7m", "matches": qim_data,
                            "unmapped": qim_miss})
    results["sources"]["qim"] = {"ok": True, "matches": len(qim_data),
                                 "unmapped": qim_miss}
    print(f"[OK] 7M体育: {len(qim_data)}场天气/阵容/战绩，未映射{len(qim_miss)}场")
except Exception as e:
    results["sources"]["qim"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] 7M体育: {e}")

# 13. FotMob: 低级别联赛赛程/比分/积分榜（北单常客：J2/J3/K2/K3/挪甲/挪乙/瑞典甲/瑞典乙/巴西乙）
#     非官方接口，逐联赛 try/except 隔离；单场无赔率(odds 恒 null)；失败不影响其他源
try:
    from fotmob import (get_standings as fm_standings, get_fixtures as fm_fixtures,
                        get_matches as fm_matches, FOTMOB_LOW_LEAGUES)
    from team_names import lookup as _tm_lookup, add_team as _tm_add
    fm_report, fm_data = {}, {}
    fm_new_teams = 0
    for lg in FOTMOB_LOW_LEAGUES:
        try:
            st = fm_standings(lg)
            fx = fm_fixtures(lg)
            # 按需补 team_id_map.json（fotmob_id）
            for t in st:
                if not _tm_lookup(t["name"]):
                    if _tm_add(t["name"], {"name": t["name"],
                                           "fotmob_id": t["fotmob_id"]}):
                        fm_new_teams += 1
            fm_data[lg] = {"standings": st, "fixtures": fx}
            fm_report[lg] = {
                "ok": True, "teams": len(st), "fixtures": len(fx),
                "finished": sum(1 for x in fx if x["finished"]),
            }
            print(f"  - FotMob {lg}: {len(st)}队 {len(fx)}场")
        except Exception as e:
            fm_report[lg] = {"ok": False, "error": str(e)[:150]}
            print(f"  - FotMob {lg} 失败: {e}")
    try:
        fm_today = fm_matches()
        fm_report["_today"] = {"ok": True, "matches": len(fm_today)}
    except Exception as e:
        fm_today = []
        fm_report["_today"] = {"ok": False, "error": str(e)[:150]}
        print(f"  - FotMob 当日赛程失败: {e}")
    save("fotmob_lowleagues", {"leagues": fm_data, "today_matches": fm_today})
    fm_ok_n = sum(1 for l in FOTMOB_LOW_LEAGUES
                  if fm_report.get(l, {}).get("ok"))
    results["sources"]["fotmob"] = {
        "ok": fm_ok_n > 0, "leagues": fm_report,
        "new_teams_mapped": fm_new_teams,
        "note": "非官方接口；单场无赔率(odds恒null)；失败已按联赛隔离",
    }
    print(f"[OK] FotMob: {fm_ok_n}/{len(FOTMOB_LOW_LEAGUES)}联赛，"
          f"新映射{fm_new_teams}队")
except Exception as e:
    results["sources"]["fotmob"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] FotMob: {e}")

# 14. 北单官方SP: 澳客六玩法即时SP + 500彩票交叉验证（2026-10-06 用户批准接入）
try:
    from beidan import get_all_sp, get_500_headers
    bd_merged = get_all_sp()
    bd_matches = {}
    for (home, away), v in bd_merged.items():
        bd_matches[f"{home}vs{away}"] = v
    bd_500 = []
    try:
        bd_500 = get_500_headers()
    except Exception as e:
        print(f"  - 500北单交叉验证失败: {e}")
    save("beidan_sp", {"source": "okooo",
                       "matches": bd_matches,
                       "w500_crosscheck": bd_500})
    n_wdl = sum(1 for v in bd_matches.values() if v.get("sp_wdl"))
    results["sources"]["beidan_sp"] = {
        "ok": True, "matches": len(bd_matches),
        "with_wdl": n_wdl, "w500_cross": len(bd_500),
        "note": "澳客六玩法即时SP；WL为澳客自有让球胜负盘(0.5盘/两项)，"
                "非官方北单让球胜平负，仅作市场信号",
    }
    print(f"[OK] 北单SP: {len(bd_matches)}场（胜平负 {n_wdl}场），"
          f"500交叉 {len(bd_500)}场")
except Exception as e:
    results["sources"]["beidan_sp"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] 北单SP: {e}")

# 15. soccerdata: Understat xG + ClubElo（2026-10-09 接入）
#     Understat 覆盖5联赛（英超/西甲/意甲/德甲/法甲）；ClubElo本机被墙，失败跳过
try:
    from soccerdata import get_understat_xg, get_club_elo
    sc_report, sc_data = {}, {}
    for lg in ["英超", "西甲", "意甲", "德甲", "法甲"]:
        try:
            xg = get_understat_xg(lg, refresh=True)
            sc_data[lg] = xg
            sc_report[lg] = {"ok": True, "teams": len(xg)}
        except Exception as e:
            sc_report[lg] = {"ok": False, "error": str(e)[:150]}
            print(f"  - soccerdata {lg} xG失败: {e}")
    try:
        elo = get_club_elo(refresh=True)
        sc_report["_elo"] = {"ok": True, "teams": len(elo)}
    except Exception as e:
        elo = []
        sc_report["_elo"] = {"ok": False, "error": str(e)[:150]}
        print(f"  - soccerdata ClubElo失败: {e}")
    save("soccerdata_xg", {"leagues": sc_data, "elo": elo})
    sc_ok = sum(1 for k, v in sc_report.items()
                if k != "_elo" and v.get("ok"))
    results["sources"]["soccerdata"] = {
        "ok": sc_ok > 0, "leagues": sc_report,
        "note": "Understat xG(5联赛)；ClubElo本机被墙，仅阿里云可用",
    }
    print(f"[OK] soccerdata: {sc_ok}/5联赛xG，ClubElo"
          f"{'OK' if sc_report['_elo']['ok'] else '失败(被墙)'}")
except Exception as e:
    results["sources"]["soccerdata"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] soccerdata: {e}")

# 16. OddsPapi: 350+博彩公司赔率（2026-10-09 接入）
#     免费250req/月；历史赔率端点免费不计配额。无key时整节跳过（用户需注册）。
try:
    _op_key = os.environ.get("ODDSPAPI_KEY")
    if not _op_key:
        results["sources"]["oddspapi"] = {
            "ok": False, "skipped": True,
            "error": "ODDSPAPI_KEY 未设置（oddspapi.io注册→Secure Vault授权）",
        }
        print("[SKIP] OddsPapi: 无key，跳过（用户注册后启用）")
    else:
        from oddspapi import get_fixtures as op_fixtures, get_odds as op_odds, \
            get_quota as op_quota
        op_fx = op_fixtures()
        op_odds_data, op_n = {}, 0
        for fx in op_fx[:20]:  # 每日上限20场，省配额
            try:
                od = op_odds(fx["fixture_id"])
                if od:
                    op_odds_data[str(fx["fixture_id"])] = od
                    op_n += 1
            except Exception as e:
                print(f"  - OddsPapi fixture {fx['fixture_id']}失败: {e}")
        save("oddspapi_snapshot", op_odds_data)
        _q = op_quota()
        results["sources"]["oddspapi"] = {
            "ok": True, "fixtures": len(op_fx), "with_odds": op_n,
            "quota_used": _q.get("used"), "quota_remaining": _q.get("remaining"),
        }
        print(f"[OK] OddsPapi: {len(op_fx)}场赛程，{op_n}场有赔率，"
              f"本月已用{_q.get('used')}剩{_q.get('remaining')}")
except Exception as e:
    results["sources"]["oddspapi"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] OddsPapi: {e}")

# 17. premierinjuries: 英超伤病/停赛/复出（2026-10-09 接入，静态无反爬）
try:
    from premierinjuries import get_injuries
    pi_rows = get_injuries()
    save("premierinjuries", pi_rows)
    pi_status = {}
    for r in pi_rows:
        pi_status[r["status"]] = pi_status.get(r["status"], 0) + 1
    results["sources"]["premierinjuries"] = {
        "ok": True, "records": len(pi_rows), "by_status": pi_status,
    }
    print(f"[OK] premierinjuries: {len(pi_rows)}条 ({pi_status})")
except Exception as e:
    results["sources"]["premierinjuries"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] premierinjuries: {e}")

# 18. openfootball: 免费历史比分库（2026-10-09 接入；本地git仓库，每周同步）
try:
    from openfootball import sync as of_sync, get_results as of_results, \
        list_leagues as of_leagues
    _sync = of_sync()
    of_leagues = of_leagues()
    of_data, of_report = {}, {}
    for lg in ["巴甲", "英超", "西甲", "意甲", "德甲", "法甲"]:
        try:
            rs = of_results(lg, str(datetime.now().year))
            of_data[lg] = rs
            of_report[lg] = {"ok": True, "matches": len(rs)}
        except Exception as e:
            of_report[lg] = {"ok": False, "error": str(e)[:120]}
    save("openfootball_results", {"leagues": of_data, "sync": _sync})
    results["sources"]["openfootball"] = {
        "ok": True, "leagues": of_report, "sync": _sync,
        "note": "本地仓库，每周一cron同步即可，无需每日git pull",
    }
    print(f"[OK] openfootball: {len(of_leagues)}联赛可用，"
          f"同步{_sync.get('football.json', {}).get('updated', '?')}")
except Exception as e:
    results["sources"]["openfootball"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] openfootball: {e}")

# 19. ESPN summary: 单场阵容/事件/技术统计（2026-10-09 接入，espn.py扩展）
#     对当日board在售场次拉阵容（赛前约1小时放出，未公布返回空）
try:
    from espn import get_summary, LEAGUE_CODES as _ESPN_LC
    from okooo import get_board_map as _ok_bm2
    _bm = _ok_bm2("jingcai")
    es_out, es_n = {}, 0
    for (home, away), _mid in list(_bm.items())[:30]:  # 上限30场
        try:
            sm = get_summary(_mid, "eng.1")  # event_id跨联赛通用
            if sm.get("lineups", {}).get("home"):
                es_n += 1
            es_out[f"{home}vs{away}"] = {
                "lineups": sm.get("lineups"),
                "events": sm.get("events"),
                "stats": sm.get("stats"),
            }
        except Exception as e:
            print(f"  - ESPN summary {home}vs{away}失败: {e}")
    save("espn_lineups", es_out)
    results["sources"]["espn_summary"] = {
        "ok": True, "matches": len(es_out), "with_lineups": es_n,
        "note": "赛前约1小时放出阵容，未公布返回空列表",
    }
    print(f"[OK] ESPN summary: {len(es_out)}场，{es_n}场有阵容")
except Exception as e:
    results["sources"]["espn_summary"] = {"ok": False, "error": str(e)[:200]}
    print(f"[FAIL] ESPN summary: {e}")

# 保存汇总
save("_summary", results)
print(f"\n汇总已保存到 {day_dir}/_summary.json")
ok_count = sum(1 for s in results["sources"].values() if s.get("ok"))
print(f"成功 {ok_count}/{len(results['sources'])} 个源")
