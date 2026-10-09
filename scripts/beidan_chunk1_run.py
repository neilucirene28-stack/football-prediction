#!/usr/bin/env python3
"""北单 chunk1: 48场预测 (2026-10-09/10)。
数据优先级: ESPN > 7M > Titan007；拿不到战绩则跳过（铁律，不编数据）。
输出: /tmp/beidan_result1.json
"""
import sys, json, time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2/collector/sources")

from engine.predictor import predict
from engine.beidan_snapshot import write_snapshot
from espn import get_scoreboard, LEAGUE_CODES as ESPN_LEAGUES, _request as espn_request
from qim import get_game_info, _get_js_var, ANALYSE, SEARCH, HEADERS
from _http import fetch_with_retry
from opencc import OpenCC

BJ = timezone(timedelta(hours=8))
# SNAP预设已删除（P0审计：禁止预设时间戳）。快照时间用真实生成时刻。
s2t = OpenCC("s2t").convert
t2s = OpenCC("t2s").convert

ESPN_LEAGUE_MAP = {
    "J联赛": "jpn.1", "英超": "eng.1", "西甲": "esp.1", "意乙": "ita.2",
    "德甲": "ger.1", "法甲": "fra.1", "法乙": "fra.2", "英冠": "eng.2",
    "荷甲": "ned.1", "苏超": "sco.1", "芬超": "fin.1", "瑞典超": "swe.1",
}
NON_FOOTBALL = {"美职冰"}


# ---------------- ESPN ----------------
def espn_get(path, params=None, timeout=15, max_retries=2):
    from espn import BASE_URL
    url = f"{BASE_URL}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    return fetch_with_retry(url, extra_args=["--compressed"],
                            timeout=timeout, max_retries=max_retries)


def espn_team_recent(league_code, team_id, kickoff_utc, n=10):
    rows = []
    for season in ("2026", "2025"):
        try:
            data = espn_get(f"/{league_code}/teams/{team_id}/schedule",
                            {"season": season})
        except Exception:
            continue
        for e in data.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            if comp.get("status", {}).get("type", {}).get("name") != "STATUS_FULL_TIME":
                continue
            try:
                edt = datetime.fromisoformat(
                    e["date"].replace("Z", "+00:00"))
            except Exception:
                continue
            if edt >= kickoff_utc:
                continue
            cs = comp.get("competitors", [])
            h = next((c for c in cs if c.get("homeAway") == "home"), None)
            a = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not h or not a:
                continue
            try:
                hg = int(float(h.get("score", {}).get("value")))
                ag = int(float(a.get("score", {}).get("value")))
            except (TypeError, ValueError):
                continue
            mine_home = str(h.get("team", {}).get("id")) == str(team_id)
            rows.append({
                "gf": hg if mine_home else ag,
                "ga": ag if mine_home else hg,
                "venue": "H" if mine_home else "A",
                "date": e["date"][:10],
            })
    rows.sort(key=lambda r: r["date"], reverse=True)
    # 去重（双赛季可能重叠）
    seen, uniq = set(), []
    for r in rows:
        k = (r["date"], r["gf"], r["ga"], r["venue"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq[:n]


def espn_resolve(league_cn, kickoff_bj):
    """按联赛+开球时间在 ESPN scoreboard 上定位本场，返回 (home_id, away_id)。
    时间窗口内有多场则判 ambiguous。"""
    code = ESPN_LEAGUE_MAP.get(league_cn)
    if not code:
        return (None, None), "league_not_covered"
    kickoff_utc = kickoff_bj.astimezone(timezone.utc)
    seen = {}
    base_et = kickoff_utc - timedelta(hours=4)
    for d_off in (-1, 0, 1):
        ds = (base_et + timedelta(days=d_off)).strftime("%Y%m%d")
        try:
            evs = get_scoreboard(code, ds)
        except Exception:
            continue
        for ev in evs:
            seen[ev.get("event_id")] = ev
    cands = []
    for ev in seen.values():
        try:
            edt = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        except Exception:
            continue
        cands.append((abs((edt - kickoff_utc).total_seconds()), ev))
    cands.sort(key=lambda x: x[0])
    if not cands or cands[0][0] > 3 * 3600:
        return (None, None), "no_event_in_window"
    if len(cands) > 1 and cands[1][0] <= 3 * 3600:
        return (None, None), "ambiguous_multi_events"
    ev = cands[0][1]
    return (ev.get("home_id"), ev.get("away_id")), None


def get_espn_form(league_cn, home, away, kickoff_bj):
    (hid, aid), err = espn_resolve(league_cn, kickoff_bj)
    if err or not hid or not aid:
        return None, None, f"espn:{err}"
    kickoff_utc = kickoff_bj.astimezone(timezone.utc)
    code = ESPN_LEAGUE_MAP[league_cn]
    hr = espn_team_recent(code, hid, kickoff_utc)
    time.sleep(0.3)
    ar = espn_team_recent(code, aid, kickoff_utc)
    time.sleep(0.3)
    if len(hr) < 6 or len(ar) < 6:
        return None, None, f"espn:场次不足(主{len(hr)}/客{len(ar)})"
    return hr, ar, None


# ---------------- 7M ----------------
def fast_search(keyword):
    """单次尝试、无重试的 7M 搜索（防某个关键词 hanging 拖死整场）。"""
    import json as _json
    import urllib.parse, re
    url = SEARCH + "?k=" + urllib.parse.quote(keyword) + "&e=0&js=1"
    try:
        raw = fetch_with_retry(url, headers=HEADERS, timeout=12,
                               max_retries=1, parse_json=False)
    except Exception:
        return []
    txt = raw.decode("utf-8", "ignore")
    m = re.search(r"loadgame\(\[(.*)\]\);", txt, re.S)
    if not m:
        return []
    try:
        rows = _json.loads("[" + m.group(1) + "]")
    except Exception:
        return []
    return [{"mid": r[0], "home": r[1], "away": r[2],
             "score": r[3], "state": r[4]} for r in rows]
def _pair_score(inp, cand):
    """归一化后的字符重叠分：|交集| / min(长度)。容忍港式音译差异。"""
    from qim import norm_team as _norm
    ii = set(_norm(inp).replace(" ", ""))
    ci = set(_norm(cand).replace(" ", ""))
    if not ii or not ci:
        return 0.0
    return len(ii & ci) / min(len(ii), len(ci))


def qim_fuzzy_mid(home, away):
    from qim import _is_senior
    cands = {}
    kw_src = {}  # mid -> set('home'|'away')
    for kw, src in [(home, "home"), (away, "away"), (home[:2], "home"),
                    (away[:2], "away"), (s2t(home), "home"), (s2t(away), "away")]:
        if not kw:
            continue
        try:
            for r in fast_search(kw):
                if not _is_senior(r):
                    continue
                cands[r["mid"]] = r
                kw_src.setdefault(r["mid"], set()).add(src)
        except Exception:
            continue
        time.sleep(0.2)
    best, best_sc = None, -1.0
    for mid, r in cands.items():
        sh = _pair_score(home, r["home"])
        sa = _pair_score(away, r["away"])
        sh2 = _pair_score(home, r["away"])
        sa2 = _pair_score(away, r["home"])
        if sh + sa >= sh2 + sa2:
            sc, mn, mx = sh + sa, min(sh, sa), max(sh, sa)
        else:
            sc, mn, mx = sh2 + sa2, min(sh2, sa2), max(sh2, sa2)
        src = kw_src.get(mid, set())
        # 规则1：双侧字符分达标；规则2：关键词直命中一侧 + 另一侧弱匹配
        ok = (sc >= 1.0 and mn >= 0.15) or \
             (("home" in src or "away" in src) and mx >= 0.4 and mn >= 0.2)
        if ok and sc > best_sc:
            best, best_sc = mid, sc
    return best


def qim_team_recent(home, away, kickoff_bj, mid=None):
    mid = mid or qim_fuzzy_mid(home, away)
    if not mid:
        return None, None, "7m:未找到比赛mid"
    try:
        info = get_game_info(mid)
        raw = json.loads(_get_js_var(f"{ANALYSE}/{mid}/data/gameteamhistory_gb.js"))
    except Exception as e:
        return None, None, f"7m:抓取失败({str(e)[:60]})"
    taid, tbid = info.get("home_id"), info.get("away_id")
    if not taid or not tbid:
        return None, None, "7m:无球队id"
    ko_day = kickoff_bj.strftime("%Y-%m-%d")
    out = {}
    for side, tid in (("A", taid), ("B", tbid)):
        sraw = raw.get(side, {}) or {}
        allh = (sraw.get("all", {}) or {}).get("history", {}) or {}
        home_ids = set(((sraw.get("home", {}) or {}).get("history", {}) or {}).get("id", []))
        n = len(allh.get("id", []))
        rows = []
        for i in range(n):
            try:
                aid = int(allh["aid"][i])
                bid = int(allh["bid"][i])
                if aid == int(tid):
                    gf, ga = int(allh["liveA"][i]), int(allh["liveB"][i])
                elif bid == int(tid):
                    gf, ga = int(allh["liveB"][i]), int(allh["liveA"][i])
                else:
                    continue
            except (ValueError, TypeError, IndexError, KeyError):
                continue
            date = str(allh["date"][i]) if i < len(allh.get("date", [])) else ""
            try:
                y, mth, day = date.split("-")
                date = f"20{y}-{mth}-{day}"
            except (ValueError, AttributeError):
                pass
            if date >= ko_day:
                continue
            rid = allh["id"][i]
            rows.append({"gf": gf, "ga": ga,
                         "venue": "H" if rid in home_ids else "A",
                         "date": date})
        out[side] = rows[:10]
    hr, ar = out.get("A", []), out.get("B", [])
    if len(hr) < 6 or len(ar) < 6:
        return None, None, f"7m:场次不足(主{len(hr)}/客{len(ar)})"
    return hr, ar, None


# ---------------- Titan007 (降级，预期机房IP被拒) ----------------
def titan_probe():
    import subprocess
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", "12", "-x", "http://hatch-egress-proxy:3128",
             "-o", "/dev/null", "-w", "%{http_code}",
             "https://zq.titan007.com/analysis/1cn.htm"],
            capture_output=True, text=True, timeout=20)
        code = (r.stdout or "").strip()
        return code not in ("", "000")
    except Exception:
        return False


# ---------------- 预测 ----------------
HF_CN = {"3": "胜", "1": "平", "0": "负"}


def hf_label(key):
    return "".join(HF_CN.get(p, p) for p in key.split("-"))


def predict_one(m):
    seq, home, away = m["seq"], m["home"], m["away"]
    league, kickoff = m["league"], m["kickoff"]
    # Fix 2: 让球线缺失时保持None，不默认成0（0是有含义的让球线）
    _hc_raw = m.get("handicap")
    base = {"seq": seq, "home": home, "away": away, "kickoff": kickoff,
            "league": league, "handicap": _hc_raw}

    if league in NON_FOOTBALL:
        return {**base, "status": "skipped", "reason": "非足球赛事(冰球)，足球引擎不适用"}

    try:
        kickoff_bj = datetime.strptime(kickoff, "%Y-%m-%d %H:%M").replace(tzinfo=BJ)
    except ValueError:
        return {**base, "status": "skipped", "reason": f"开球时间格式异常:{kickoff}"}

    hr = ar = None
    form_source, form_err = None, None
    form_mid = None
    if league in ESPN_LEAGUE_MAP:
        hr, ar, form_err = get_espn_form(league, home, away, kickoff_bj)
        if hr:
            form_source = "espn"
    if not hr:
        form_mid = qim_fuzzy_mid(home, away)
        if form_mid:
            hr, ar, err2 = qim_team_recent(home, away, kickoff_bj, mid=form_mid)
        else:
            hr, ar, err2 = None, None, "7m:未找到比赛mid"
        if hr:
            form_source = "7m"
            form_err = None
        else:
            form_err = "; ".join(x for x in [form_err, err2] if x)
    if not hr:
        ok = titan_probe()
        reason = (form_err or "战绩缺失") + ("; titan007子域被拒" if not ok else "; titan007未覆盖")
        return {**base, "status": "skipped", "reason": reason}

    sp = m.get("sp_wdl") or {}
    odds = None
    if sp.get("胜") is not None and sp.get("平") is not None and sp.get("负") is not None:
        odds = {"home": sp["胜"], "draw": sp["平"], "away": sp["负"]}

    hc = base["handicap"]
    # 快照链路：记录战绩数据实际到手的时间（真实证据，非预设）
    t_data_ready = datetime.now(timezone.utc).isoformat()
    payload = {
        "home": home, "away": away,
        "kickoff_at": kickoff_bj.strftime("%Y-%m-%dT%H:%M:00+08:00"),
        "snapshot_at": t_data_ready,  # 真实生成时刻（P0审计：禁预设）
        "competition": league,
        "home_recent": [{"gf": r["gf"], "ga": r["ga"], "venue": r["venue"]} for r in hr],
        "away_recent": [{"gf": r["gf"], "ga": r["ga"], "venue": r["venue"]} for r in ar],
        "odds": odds,
        "handicap": hc,  # None=缺失，不默认0
        "handicap_line": int(hc) if hc is not None else None,
        "league_avg_goals": 2.70,
    }
    try:
        res = predict(payload, model="beidan")
    except Exception as e:
        return {**base, "status": "skipped",
                "reason": f"引擎异常:{str(e)[:120]}", "form_source": form_source}
    # 快照链路：generated_at = 引擎实际跑完的真实时刻（禁止预设/冒充）
    t_generated = datetime.now(timezone.utc).isoformat()
    if res.get("status") == "insufficient_data":
        return {**base, "status": "skipped",
                "reason": f"insufficient_data:{res.get('reason','')[:100]}",
                "form_source": form_source}

    d = res.get("derivatives", {}) or {}
    bd = res.get("beidan", {}) or {}
    probs = {"home": res["p_home"], "draw": res["p_draw"], "away": res["p_away"]}
    wdl_pick = max(probs, key=probs.get)
    wdl_cn = {"home": "主胜", "draw": "平局", "away": "客胜"}[wdl_pick]

    h1x2 = d.get("handicap_1x2") or {}
    rq_pick, rq_prob = None, None
    if h1x2:
        hp = {"让胜": h1x2.get("p_home", 0), "让平": h1x2.get("p_draw", 0),
              "让负": h1x2.get("p_away", 0)}
        rq_pick = max(hp, key=hp.get)
        rq_prob = round(hp[rq_pick], 4)

    top3 = [(s["score"], s["prob"]) for s in (d.get("top_scores") or [])[:3]]
    tg = d.get("total_goals_exact") or {}
    tg_pick, tg_prob = None, None
    if tg:
        tg_pick = max(tg, key=lambda k: tg[k])
        tg_prob = tg[tg_pick]
    hf = d.get("half_full_1x2") or {}
    hf_pick, hf_prob = None, None
    if hf:
        k = max(hf, key=lambda x: hf[x])
        hf_pick, hf_prob = hf_label(k), hf[k]
    # 上下单双：上盘=总进球>=3
    ou_pick, ou_prob = None, None
    if tg:
        combos = {"上单": 0.0, "上双": 0.0, "下单": 0.0, "下双": 0.0}
        for k, p in tg.items():
            try:
                g = int(str(k).rstrip("+"))
            except ValueError:
                continue
            up = g >= 3
            odd = (g % 2 == 1)
            combos[("上" if up else "下") + ("单" if odd else "双")] += p
        ou_pick = max(combos, key=combos.get)
        ou_prob = round(combos[ou_pick], 4)

    # ---- 北单快照链路（append-only，不影响预测逻辑） ----
    # 快照失败不阻断预测，只打印警告。
    try:
        snap_match = {
            "lottery_no": m.get("lottery_no", "unknown"),
            "seq": seq,
            "league": league,
            "home": home,
            "away": away,
            "kickoff": kickoff_bj.isoformat(),
            "generated_at": t_generated,
            "available_at": {form_source: t_data_ready} if form_source else {},
            "handicap_line": hc,  # None=缺失，不默认0
            # Fix 1: sp collected_at=null（无SP源真实采集证据，不准用t_data_ready替代）
            # write_snapshot 会因无SP证据自动标 observation_only=true
            "sp_snapshot": {"sp_wdl": sp, "collected_at": None} if sp else None,
            "data_completeness": {"form_source": form_source},
            "skipped": False,
        }
        snap_rec = write_snapshot(snap_match, res)
        snapshot_id = snap_rec["snapshot_id"]
    except Exception as e:
        snapshot_id = None
        print(f"[snapshot] seq={seq} 写入失败（不阻断预测）: {e}", flush=True)

    return {
        **base, "status": "ok", "form_source": form_source,
        "form_mid": form_mid,
        "grade": res.get("grade"), "confidence": res.get("confidence"),
        "p_home": round(res["p_home"], 4), "p_draw": round(res["p_draw"], 4),
        "p_away": round(res["p_away"], 4),
        "胜平负预测": wdl_cn,
        "让球预测": {"结果": rq_pick, "概率": rq_prob} if rq_pick else None,
        "比分Top3": [{"比分": s, "概率": p} for s, p in top3],
        "总进球最可能": {"进球数": tg_pick, "概率": tg_prob} if tg_pick else None,
        "半全场首选": {"结果": hf_pick, "概率": hf_prob} if hf_pick else None,
        "上下单双首选": {"结果": ou_pick, "概率": ou_prob} if ou_pick else None,
        "beidan校准首选概率": bd.get("calibrated_p_top_sp"),
        "beidan让球校准概率": bd.get("calibrated_p_top_rq"),
        "冷门风险档": bd.get("upset_risk_tier"),
        "sp_wdl": sp,
        "snapshot_id": snapshot_id,
    }


def main():
    matches = json.load(open("/tmp/beidan_chunk1.json", encoding="utf-8"))
    print(f"共 {len(matches)} 场", flush=True)
    results = []
    for i, m in enumerate(matches):
        t0 = time.time()
        r = predict_one(m)
        dt = time.time() - t0
        results.append(r)
        tag = "OK " if r["status"] == "ok" else "SKIP"
        print(f"[{i+1:2d}/{len(matches)}] {tag} seq={m['seq']} {m['home']}vs{m['away']} "
              f"({r.get('form_source') or r.get('reason','')[:40]}) {dt:.1f}s", flush=True)
    ok = sum(1 for r in results if r["status"] == "ok")
    sk = [r for r in results if r["status"] != "ok"]
    from collections import Counter
    reasons = Counter(r.get("reason", "?") for r in sk)
    print(f"\n成功 {ok} 场，跳过 {len(sk)} 场")
    for reason, c in reasons.most_common():
        print(f"  x{c}: {reason[:90]}")
    json.dump(results, open("/tmp/beidan_result1.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("已写 /tmp/beidan_result1.json")


if __name__ == "__main__":
    main()
