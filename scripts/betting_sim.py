#!/usr/bin/env python3
"""竞彩模拟投注 v2：混合过关 + 单关（仅单关开售场次）。

全程模拟，不产生任何真实投注。用法：
    python3 scripts/betting_sim.py select --predictions data/predictions/2026-10-15-jingcai.json
    python3 scripts/betting_sim.py settle --scores /tmp/scores.json
    python3 scripts/betting_sim.py report

select 时实时抓取 500.com 四页面官方 SP（269 胜平负/让球、271 比分、270 进球数、
272 半全场）及每场每玩法的单关/过关标志；模型概率来自预测文件。
scores.json 格式：[{"date": "2026-10-15", "num": "周四001", "score": "2-1", "ht_score": "1-0"}]
  推迟/取消用 "postponed"。半全场腿需 ht_score，缺失则暂不结算。
账本幂等：同一天重复 select 不重复下注；已结算票不重复结算。
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SIM_DIR = BASE / "data" / "betting-sim"
LEDGER = SIM_DIR / "ledger.jsonl"
BANKROLL = SIM_DIR / "bankroll.json"

START_BANKROLL = 10000.0
EV_MIN = 0.08
PARLAY_EV_MIN = 0.15
# 防彩票注护栏：长串冷门市场（比分/进球数）的尾部概率估计误差极大、
# 水位又高（比分 overround 约 1.3），概率过低或赔率过高的腿一律不投。
MIN_LEG_PROB = 0.10
MAX_LEG_ODDS = 12.0
KELLY_FRACTION = 0.25
MAX_STAKE_PCT = 0.03
MIN_STAKE_PCT = 0.003
MAX_SINGLES_PER_DAY = 2
MAX_DAILY_EXPOSURE_PCT = 0.15
ALLOWED_CONFIDENCE = {"A", "B"}
ALLOWED_GRADE = {"A", "B"}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_PAGES = {
    269: "https://trade.500.com/jczq/?playid=269",
    271: "https://trade.500.com/jczq/?playid=271",
    270: "https://trade.500.com/jczq/?playid=270",
    272: "https://trade.500.com/jczq/?playid=272",
}
# 500.com data-type -> 玩法
_TYPE_TO_PLAY = {"nspf": "1X2", "spf": "H1X2", "bf": "SCORE",
                 "jqs": "GOALS", "bqc": "HTFT"}
# subactive key -> 玩法
_FLAG_TO_PLAY = {"nspf": "1X2", "spf": "H1X2", "bf": "SCORE",
                 "jq": "GOALS", "bq": "HTFT"}
_HTFT_CODE = {"胜胜": "3-3", "胜平": "3-1", "胜负": "3-0",
              "平胜": "1-3", "平平": "1-1", "平负": "1-0",
              "负胜": "0-3", "负平": "0-1", "负负": "0-0"}
_HTFT_LABEL = {v: k for k, v in _HTFT_CODE.items()}
_WDL_CODE = {"home": "3", "draw": "1", "away": "0"}
_WDL_CN = {"home": "胜", "draw": "平", "away": "负"}
_ROW_RE = re.compile(r'<tr class="bet-tb-tr"[^>]*data-matchnum="([^"]+)"[^>]*>')
_BTN_RE = re.compile(
    r'data-type="(nspf|spf|bf|jqs|bqc)" data-value="([^"]+)" data-sp="([\d.]+)"')
_ATTR_RE = re.compile(r'data-(matchdate|matchtime|rangqiu|homesxname|awaysxname|subactive)="([^"]*)"')


def _fetch_page(url):
    """curl 抓取（本机 urllib 不走出口代理，改用 curl）。"""
    out = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-H", f"User-Agent: {_UA}", url],
        capture_output=True, timeout=40)
    if out.returncode != 0:
        raise RuntimeError(f"curl failed rc={out.returncode} for {url}")
    return out.stdout.decode("gbk", errors="ignore")


def fetch_500_odds():
    """抓取 500.com 四页面，返回 {场次序号: {home, away, kickoff, handicap, flags, odds}}。

    odds: {玩法: {赔率键: SP}}；flags: {玩法: {"dg": bool, "gg": bool}}。
    """
    out = {}
    for playid, url in _PAGES.items():
        html = _fetch_page(url)
        rows = list(_ROW_RE.finditer(html))
        for idx, row in enumerate(rows):
            num = row.group(1)
            end = rows[idx + 1].start() if idx + 1 < len(rows) else len(html)
            tag = row.group(0)
            attrs = dict(_ATTR_RE.findall(tag))
            seg = html[row.start():end]
            entry = out.setdefault(num, {"odds": {}, "flags": {}})
            entry["home"] = attrs.get("homesxname", "")
            entry["away"] = attrs.get("awaysxname", "")
            if attrs.get("matchdate") and attrs.get("matchtime"):
                entry["kickoff"] = f"{attrs['matchdate']}T{attrs['matchtime']}:00+08:00"
            if attrs.get("rangqiu") not in (None, ""):
                try:
                    entry["handicap"] = int(attrs["rangqiu"])
                except ValueError:
                    pass
            for part in (attrs.get("subactive") or "").split(","):
                if ":" not in part:
                    continue
                key, val = part.split(":", 1)
                m = re.match(r"(.+?)(dg|gg)$", key)
                if not m or m.group(1) not in _FLAG_TO_PLAY:
                    continue
                play = _FLAG_TO_PLAY[m.group(1)]
                entry["flags"].setdefault(play, {})[m.group(2)] = (val == "1")
            for typ, value, sp in _BTN_RE.findall(seg):
                play = _TYPE_TO_PLAY[typ]
                entry["odds"].setdefault(play, {})[value] = float(sp)
    return out


def model_legs(match):
    """从模型结果构造全部候选腿：[{play, option, odds_key, p, label}]。"""
    r = match.get("result") or {}
    der = r.get("derivatives") or {}
    legs = []
    for outcome in ("home", "draw", "away"):
        p = r.get({"home": "p_home", "draw": "p_draw", "away": "p_away"}[outcome])
        if p is not None:
            legs.append({"play": "1X2", "option": outcome,
                         "odds_key": _WDL_CODE[outcome], "p": p,
                         "label": f"胜平负{_WDL_CN[outcome]}"})
    h = der.get("handicap_1x2") or {}
    for outcome in ("home", "draw", "away"):
        p = h.get(f"p_{outcome}_full")
        if p is not None:
            legs.append({"play": "H1X2", "option": outcome,
                         "odds_key": _WDL_CODE[outcome], "p": p,
                         "label": f"让球{_WDL_CN[outcome]}"})
    for item in der.get("top_scores") or []:
        legs.append({"play": "SCORE", "option": item["score"],
                     "odds_key": item["score"].replace("-", ":"),
                     "p": item["prob"], "label": f"比分{item['score']}"})
    for k, p in (der.get("total_goals_exact_full") or {}).items():
        opt = "7" if k == "7+" else k
        legs.append({"play": "GOALS", "option": opt, "odds_key": opt, "p": p,
                     "label": f"总进球{'7+' if opt == '7' else opt}"})
    for label, p in (der.get("half_full_1x2_full") or {}).items():
        legs.append({"play": "HTFT", "option": label,
                     "odds_key": _HTFT_CODE.get(label, ""), "p": p,
                     "label": f"半全场{label}"})
    return legs


def _kelly_stake(p, odds, balance):
    b = odds - 1.0
    if b <= 0:
        return 0.0
    kelly = (p * b - (1 - p)) / b
    if kelly <= 0:
        return 0.0
    stake = KELLY_FRACTION * kelly * balance
    return min(stake, MAX_STAKE_PCT * balance)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_ledger():
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def save_ledger(rows):
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8")


def load_bankroll():
    if BANKROLL.exists():
        return json.loads(BANKROLL.read_text(encoding="utf-8"))
    return {"start": START_BANKROLL, "balance": START_BANKROLL, "events": []}


def save_bankroll(state):
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    BANKROLL.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_event(state, kind, delta, note):
    state["balance"] = round(state["balance"] + delta, 2)
    state["events"].append({
        "at": _now_iso(), "type": kind,
        "delta": round(delta, 2), "balance": state["balance"], "note": note})


def cmd_select(args):
    preds = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    date = args.date or datetime.now().astimezone().strftime("%Y-%m-%d")
    ledger = load_ledger()
    if any(t.get("date") == date for t in ledger):
        print(json.dumps({"status": "already_placed", "date": date}, ensure_ascii=False))
        return 0
    state = load_bankroll()
    balance = state["balance"]
    if balance <= START_BANKROLL * 0.70:
        print(json.dumps({"status": "stopped", "reason": "累计回撤超30%，实验已终止",
                          "balance": balance}, ensure_ascii=False))
        return 0
    try:
        odds = fetch_500_odds()
    except Exception as e:  # noqa: BLE001 - 抓不到赔率就不下注
        print(json.dumps({"status": "odds_fetch_failed", "error": f"{type(e).__name__}: {e}"},
                         ensure_ascii=False))
        return 0
    now = datetime.now(timezone.utc)
    best_per_match = {}
    for m in preds.get("matches", []):
        r = m.get("result") or {}
        if r.get("status") != "ok":
            continue
        if r.get("confidence") not in ALLOWED_CONFIDENCE:
            continue
        if r.get("grade") not in ALLOWED_GRADE:
            continue
        if r.get("divergence") is not None:
            continue
        try:
            kickoff = datetime.fromisoformat(m["kickoff"])
        except (KeyError, ValueError):
            continue
        if kickoff <= now:
            continue
        num = m.get("num")
        od = odds.get(num) or {}
        match_odds = od.get("odds") or {}
        flags = od.get("flags") or {}
        for leg in model_legs(m):
            if leg["p"] < MIN_LEG_PROB:
                continue
            sp = match_odds.get(leg["play"], {}).get(leg["odds_key"])
            if not sp or sp > MAX_LEG_ODDS:
                continue
            ev = leg["p"] * sp - 1.0
            if ev < EV_MIN:
                continue
            leg = dict(leg, num=num, home=m.get("home"), away=m.get("away"),
                       kickoff=m.get("kickoff"),
                       handicap=od.get("handicap", m.get("handicap")),
                       odds=sp, ev=round(ev, 4),
                       dg=bool(flags.get(leg["play"], {}).get("dg")),
                       gg=bool(flags.get(leg["play"], {}).get("gg")))
            key = num
            if key not in best_per_match or leg["ev"] > best_per_match[key]["ev"]:
                best_per_match[key] = leg
    cands = sorted(best_per_match.values(), key=lambda c: c["ev"], reverse=True)
    tickets = []
    total_stake = 0.0
    cap = MAX_DAILY_EXPOSURE_PCT * balance
    # 1) 混串票：取 EV 最高的 3 场（gg=1 的腿），不足 3 场则 2 场
    parlay_legs = [c for c in cands if c["gg"]][:3]
    if len(parlay_legs) >= 2:
        p_comb = 1.0
        odds_comb = 1.0
        parlay_ev = 1.0
        for c in parlay_legs:
            p_comb *= c["p"]
            odds_comb *= c["odds"]
            parlay_ev *= (1 + c["ev"])
        parlay_ev -= 1.0
        if parlay_ev >= PARLAY_EV_MIN:
            stake = _kelly_stake(p_comb, odds_comb, balance)
            if stake >= MIN_STAKE_PCT * balance and total_stake + stake <= cap:
                tickets.append({
                    "date": date, "ticket_id": f"{date}-P1", "type": "parlay",
                    "legs": [{k: c[k] for k in
                              ("num", "home", "away", "kickoff", "handicap", "play",
                               "option", "label", "p", "odds", "ev")} for c in parlay_legs],
                    "p_combined": round(p_comb, 4),
                    "odds_combined": round(odds_comb, 2),
                    "ev": round(parlay_ev, 4),
                    "stake": round(stake, 2),
                    "potential_return": round(stake * odds_comb, 2),
                    "status": "open", "placed_at": _now_iso(),
                })
                total_stake += stake
    used_nums = {c["num"] for t in tickets for c in t["legs"]}
    # 2) 单关票：串外剩余腿中单关开售的，最多 2 注
    singles = [c for c in cands if c["num"] not in used_nums and c["dg"]]
    for c in singles[:MAX_SINGLES_PER_DAY]:
        stake = _kelly_stake(c["p"], c["odds"], balance)
        if stake < MIN_STAKE_PCT * balance:
            continue
        if total_stake + stake > cap:
            continue
        tickets.append({
            "date": date, "ticket_id": f"{date}-S{len(tickets) + 1}", "type": "single",
            "legs": [{k: c[k] for k in
                      ("num", "home", "away", "kickoff", "handicap", "play",
                       "option", "label", "p", "odds", "ev")}],
            "p_combined": round(c["p"], 4),
            "odds_combined": round(c["odds"], 2),
            "ev": round(c["ev"], 4),
            "stake": round(stake, 2),
            "potential_return": round(stake * c["odds"], 2),
            "status": "open", "placed_at": _now_iso(),
        })
        total_stake += stake
    if not tickets:
        print(json.dumps({"status": "no_bets", "date": date, "balance": balance},
                         ensure_ascii=False))
        return 0
    for t in tickets:
        ledger.append(t)
        _record_event(state, "place", -t["stake"],
                      f"{t['ticket_id']} {t['type']} x{t['odds_combined']} "
                      f"{'+'.join(c['label'] for c in t['legs'])}")
    save_ledger(ledger)
    save_bankroll(state)
    print(json.dumps({"status": "placed", "date": date, "n_tickets": len(tickets),
                      "total_stake": round(total_stake, 2), "balance": state["balance"],
                      "tickets": tickets}, ensure_ascii=False, indent=2))
    return 0


def _wdl(hg, ag):
    return "home" if hg > ag else ("away" if ag > hg else "draw")


def _eval_leg(leg, score, ht_score):
    """返回 win / loss / void / unresolved。"""
    if score is None:
        return "unresolved"
    if str(score).lower() in ("postponed", "cancelled", "void"):
        return "void"
    try:
        hg, ag = (int(x) for x in str(score).split("-"))
    except ValueError:
        return "unresolved"
    play = leg["play"]
    if play == "1X2":
        return "win" if _wdl(hg, ag) == leg["option"] else "loss"
    if play == "H1X2":
        hcp = leg.get("handicap")
        if hcp is None:
            return "unresolved"
        return "win" if _wdl(hg + int(hcp), ag) == leg["option"] else "loss"
    if play == "SCORE":
        return "win" if f"{hg}-{ag}" == leg["option"] else "loss"
    if play == "GOALS":
        total = hg + ag
        opt = leg["option"]
        hit = (total >= 7) if opt == "7" else (total == int(opt))
        return "win" if hit else "loss"
    if play == "HTFT":
        if ht_score is None:
            return "unresolved"
        try:
            hh, ha = (int(x) for x in str(ht_score).split("-"))
        except ValueError:
            return "unresolved"
        code = f"{_WDL_CODE[_wdl(hh, ha)]}-{_WDL_CODE[_wdl(hg, ag)]}"
        return "win" if _HTFT_LABEL.get(code) == leg["option"] else "loss"
    return "unresolved"


def cmd_settle(args):
    scores = json.loads(Path(args.scores).read_text(encoding="utf-8"))
    by_num, by_match = {}, {}
    for s in scores:
        if s.get("num"):
            by_num[(s.get("date"), s["num"])] = s
        if s.get("match"):
            by_match[s["match"]] = s
    ledger = load_ledger()
    state = load_bankroll()
    settled = []
    for t in ledger:
        if t.get("status") != "open":
            continue
        leg_results = []
        any_pending = False
        for leg in t["legs"]:
            s = by_num.get((t.get("date"), leg["num"])) or \
                by_match.get(f"{leg.get('home')} vs {leg.get('away')}")
            if s is None:
                leg["leg_result"] = "pending"
                any_pending = True
                continue
            r = _eval_leg(leg, s.get("score"), s.get("ht_score"))
            if r == "unresolved":
                leg["leg_result"] = "pending"
                any_pending = True
                continue
            leg["leg_result"] = r
            leg["score"] = s.get("score")
            if s.get("ht_score"):
                leg["ht_score"] = s.get("ht_score")
            leg_results.append(r)
        # 串关任一腿输了，整票已定为输，不必等其余腿
        if "loss" in leg_results:
            t["settled_at"] = _now_iso()
            t["status"] = "settled"
            t["result"] = "loss"
            t["pnl"] = -t["stake"]
            _record_event(state, "settle_loss", 0.0,
                          f"{t['ticket_id']} 已定输（某腿输）")
            settled.append(t)
            continue
        if any_pending:
            for leg in t["legs"]:
                leg.pop("leg_result", None)
                leg.pop("score", None)
                leg.pop("ht_score", None)
            continue
        t["settled_at"] = _now_iso()
        if "loss" in leg_results:
            t["status"] = "settled"
            t["result"] = "loss"
            t["pnl"] = -t["stake"]
            _record_event(state, "settle_loss", 0.0,
                          f"{t['ticket_id']} 未中")
        elif all(r == "void" for r in leg_results):
            t["status"] = "void"
            t["result"] = "void"
            t["pnl"] = 0.0
            _record_event(state, "void_refund", t["stake"],
                          f"{t['ticket_id']} 全部腿取消退款")
        else:
            ret = t["stake"]
            for leg, r in zip(t["legs"], leg_results):
                if r == "win":
                    ret *= leg["odds"]
            t["status"] = "settled"
            t["result"] = "win"
            t["pnl"] = round(ret - t["stake"], 2)
            _record_event(state, "settle_win", round(ret, 2),
                          f"{t['ticket_id']} 命中返还 {round(ret, 2)}")
        settled.append(t)
    save_ledger(ledger)
    save_bankroll(state)
    print(json.dumps({"status": "settled", "n": len(settled),
                      "balance": state["balance"], "tickets": settled},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_report(args):
    ledger = load_ledger()
    state = load_bankroll()
    settled = [t for t in ledger if t.get("status") == "settled"]
    wins = [t for t in settled if t.get("result") == "win"]
    settled_pnl = round(sum(t.get("pnl", 0) for t in settled), 2)
    exposure = round(sum(t["stake"] for t in ledger if t.get("status") == "open"), 2)
    print(json.dumps({
        "start": START_BANKROLL, "balance": state["balance"],
        "settled_pnl": settled_pnl,
        "roi_pct": round(settled_pnl / START_BANKROLL * 100, 2),
        "exposure": exposure,
        "total_staked": round(sum(
            t["stake"] for t in ledger if t.get("status") in ("settled", "open")), 2),
        "n_tickets": len(ledger), "n_settled": len(settled),
        "n_open": sum(1 for t in ledger if t.get("status") == "open"),
        "win_rate": round(len(wins) / len(settled), 3) if settled else None,
        "open_tickets": [t for t in ledger if t.get("status") == "open"],
    }, ensure_ascii=False, indent=2))
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("select")
    s.add_argument("--predictions", required=True)
    s.add_argument("--date")
    s.set_defaults(fn=cmd_select)
    s2 = sub.add_parser("settle")
    s2.add_argument("--scores", required=True)
    s2.set_defaults(fn=cmd_settle)
    s3 = sub.add_parser("report")
    s3.set_defaults(fn=cmd_report)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
