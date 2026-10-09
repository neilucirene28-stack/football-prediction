#!/usr/bin/env python3
"""红黄牌 walk-forward 验证（MVP-6）。

训练：2122–2324 赛季；测试：2425 赛季按日期顺序，expanding window
（每场比赛只用它开球之前的数据，无前视）。
评估：总黄牌 O/U 3.5、O/U 4.5 的 Brier / LogLoss / 命中率，
exp_total 的 MAE；对照组为"恒预测联赛均值"。
裁判：实测两档——(a) shipped 配置 referee=None；(b) oracle 裁判（假设任命已知，
仅作价值上界参考，标注非当前系统能力）。
注意：football-data 无牌数盘口，只能对实际结果做 proper scoring，不能对市场。
样本不够/结果不佳 → 只报数，不调参。
"""
import csv
import glob
import math
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from engine.cards import (SHRINK_PRIOR, _pois_over, _referee_effect,  # noqa: E402
                          _shrunk_rate)

CSV_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cards_csv")
LEAGUES = ["E0", "SP1", "I1", "D1", "F1"]


def _parse_date(s):
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def load_matches():
    rows = []
    for f in glob.glob(os.path.join(CSV_DIR, "*.csv")):
        with open(f, encoding="utf-8-sig") as fh:
            for x in csv.DictReader(fh):
                d = _parse_date(x.get("Date", ""))
                if not d or not x.get("HomeTeam") or not x.get("AwayTeam"):
                    continue
                try:
                    hy, ay = int(float(x["HY"])), int(float(x["AY"]))
                    hr, ar = int(float(x["HR"])), int(float(x["AR"]))
                except (KeyError, TypeError, ValueError):
                    continue
                rows.append({"date": d, "league": x["Div"],
                             "home": x["HomeTeam"], "away": x["AwayTeam"],
                             "hy": hy, "ay": ay, "hr": hr, "ar": ar,
                             "ref": (x.get("Referee") or "").strip() or None})
    rows.sort(key=lambda r: r["date"])
    return rows


def predict_from_state(st, league_code, home, away, referee, use_recent=False):
    """st: {(league, team, side): [mp, yf, ya, rf]}, baselines类似。
    返回 exp_total（黄牌）。use_recent=True 时加入近5场牌数特征。"""
    lg = st["lg"][league_code]
    n = lg["n"]
    if n == 0:
        return None
    base_hy, base_ay = lg["hy"] / n, lg["ay"] / n
    base_hr, base_ar = lg["hr"] / n, lg["ar"] / n

    def recent_factor(team, side, base, n_last=5, k=3.0, w=0.3):
        """近n_last场吃牌率（收缩后）/ 联赛均值 → 混合因子。无数据返回1.0。"""
        hist = st["hist"].get((league_code, team, side), [])
        if not hist:
            return 1.0
        last = hist[-n_last:]
        tot_y = sum(y for _, y in last)
        shrunk = (tot_y + base * k) / (len(last) + k)
        return (1.0 - w) + w * (shrunk / base) if base else 1.0

    def strength(team, side):
        s = st["tm"].get((league_code, team, side))
        if not s or s[0] == 0:
            return None
        mp, yf, ya, rf = s
        base = base_hy if side == "home" else base_ay
        eat = _shrunk_rate(yf, mp, base) / base
        make = _shrunk_rate(ya, mp, base) / base
        rbase = base_hr if side == "home" else base_ar
        red = _shrunk_rate(rf, mp, rbase) / rbase if rbase else 1.0
        return eat, make, red

    hs, aws = strength(home, "home"), strength(away, "away")
    if hs is None or aws is None:
        return None
    lam_h = base_hy * hs[0] * aws[1]
    lam_a = base_ay * aws[0] * hs[1]
    if use_recent:
        lam_h *= recent_factor(home, "home", base_hy)
        lam_a *= recent_factor(away, "away", base_ay)
    # 裁判乘子（oracle 档用真实裁判；shipped 档 referee=None → 1.0）
    lg_view = {"home_yellow": base_hy, "away_yellow": base_ay,
               "home_red": base_hr, "away_red": base_ar,
               "referees": st["ref"]}
    mult, _, _ = _referee_effect(lg_view, referee)
    return min((lam_h + lam_a) * mult, 9.0)


def main() -> int:
    rows = load_matches()
    test = [r for r in rows if r["date"] >= datetime(2024, 7, 1)]
    train = [r for r in rows if r["date"] < datetime(2024, 7, 1)]
    print(f"训练 {len(train)} 场，测试 {len(test)} 场")

    st = {"lg": {}, "tm": {}, "ref": {}, "hist": {}}
    for lg in LEAGUES:
        st["lg"][lg] = {"n": 0, "hy": 0, "ay": 0, "hr": 0, "ar": 0}

    def feed(r):
        lg = st["lg"][r["league"]]
        lg["n"] += 1
        lg["hy"] += r["hy"]
        lg["ay"] += r["ay"]
        lg["hr"] += r["hr"]
        lg["ar"] += r["ar"]
        for team, side, yf, ya, rf in (
                (r["home"], "home", r["hy"], r["ay"], r["hr"]),
                (r["away"], "away", r["ay"], r["hy"], r["ar"])):
            s = st["tm"].setdefault((r["league"], team, side), [0, 0, 0, 0])
            s[0] += 1
            s[1] += yf
            s[2] += ya
            s[3] += rf
            st["hist"].setdefault((r["league"], team, side), []).append(
                (r["date"], yf))
        if r["ref"]:
            rr = st["ref"].setdefault(r["ref"], {"mp": 0, "cards": 0,
                                                 "reds": 0})
            rr["mp"] += 1
            rr["cards"] += r["hy"] + r["ay"]
            rr["reds"] += r["hr"] + r["ar"]

    for r in train:
        feed(r)

    agg = {"n": 0, "brier35": 0.0, "brier45": 0.0, "ll35": 0.0, "ll45": 0.0,
           "mae": 0.0, "base_brier35": 0.0, "hit35": 0,
           "n_oracle": 0, "brier35_oracle": 0.0,
           "brier35_recent": 0.0, "brier45_recent": 0.0, "mae_recent": 0.0,
           "hit35_recent": 0}
    for r in test:
        lam = predict_from_state(st, r["league"], r["home"], r["away"], None)
        total = r["hy"] + r["ay"]
        if lam is None:
            feed(r)
            continue
        o35, o45 = 1.0 if total > 3.5 else 0.0, 1.0 if total > 4.5 else 0.0
        p35, p45 = _pois_over(lam, 3.5), _pois_over(lam, 4.5)
        # 近期特征档
        lam_r = predict_from_state(st, r["league"], r["home"], r["away"], None,
                                   use_recent=True)
        pr35, pr45 = _pois_over(lam_r, 3.5), _pois_over(lam_r, 4.5)
        # 基线：恒预测联赛均值
        lg = st["lg"][r["league"]]
        base_lam = (lg["hy"] + lg["ay"]) / max(lg["n"], 1)
        bp35 = _pois_over(base_lam, 3.5)
        agg["n"] += 1
        agg["brier35"] += (p35 - o35) ** 2
        agg["brier45"] += (p45 - o45) ** 2
        agg["ll35"] += -(o35 * math.log(max(p35, 1e-9)) +
                         (1 - o35) * math.log(max(1 - p35, 1e-9)))
        agg["ll45"] += -(o45 * math.log(max(p45, 1e-9)) +
                         (1 - o45) * math.log(max(1 - p45, 1e-9)))
        agg["mae"] += abs(lam - total)
        agg["base_brier35"] += (bp35 - o35) ** 2
        agg["hit35"] += ((p35 > 0.5) == bool(o35))
        agg["brier35_recent"] += (pr35 - o35) ** 2
        agg["brier45_recent"] += (pr45 - o45) ** 2
        agg["mae_recent"] += abs(lam_r - total)
        agg["hit35_recent"] += ((pr35 > 0.5) == bool(o35))
        # oracle 裁判档（仅 E0 有裁判数据）
        if r["ref"]:
            lam_o = predict_from_state(st, r["league"], r["home"], r["away"],
                                       r["ref"])
            if lam_o is not None:
                po35 = _pois_over(lam_o, 3.5)
                agg["n_oracle"] += 1
                agg["brier35_oracle"] += (po35 - o35) ** 2
        feed(r)

    n = agg["n"]
    print(f"=== walk-forward（2425赛季，expanding window，n={n}）===")
    print(f"O/U 3.5  Brier: {agg['brier35']/n:.4f}（基线恒均值 "
          f"{agg['base_brier35']/n:.4f}） LogLoss: {agg['ll35']/n:.4f} "
          f"方向命中: {agg['hit35']/n:.1%}")
    print(f"O/U 4.5  Brier: {agg['brier45']/n:.4f} "
          f"LogLoss: {agg['ll45']/n:.4f}")
    print(f"总黄牌期望 MAE: {agg['mae']/n:.2f}")
    print(f"--- 近期特征档（近5场，收缩k=3，权重0.3）---")
    print(f"O/U 3.5  Brier: {agg['brier35_recent']/n:.4f} "
          f"(Δ{agg['brier35_recent']/n - agg['brier35']/n:+.4f}) "
          f"方向命中: {agg['hit35_recent']/n:.1%}")
    print(f"O/U 4.5  Brier: {agg['brier45_recent']/n:.4f} "
          f"(Δ{agg['brier45_recent']/n - agg['brier45']/n:+.4f})")
    print(f"总黄牌期望 MAE: {agg['mae_recent']/n:.2f} "
          f"(Δ{agg['mae_recent']/n - agg['mae']/n:+.2f})")
    if agg["n_oracle"]:
        print(f"oracle裁判档（n={agg['n_oracle']}，价值上界）O/U 3.5 Brier: "
              f"{agg['brier35_oracle']/agg['n_oracle']:.4f}")
    print("注：无牌数盘口，只能对实际结果做 proper scoring；样本为单赛季，"
          "不调参。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
