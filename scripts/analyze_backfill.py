#!/usr/bin/env python3
"""回填复盘分析：校准指标 / 消融 / 背离复盘 / 亚盘让平 / 进球偏差 / 漂移。"""
import sys, json, math
from collections import defaultdict

sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
IN = "/home/hatch/workspace/football-prediction-v2/hidden_backfill_sep2026.jsonl"

IDX = {"H": 0, "D": 1, "A": 2}
NAMES = ["home", "draw", "away"]


def brier(ps, y):
    return sum((p - (1.0 if i == y else 0.0)) ** 2 for i, p in enumerate(ps))


def logloss(ps, y):
    return -math.log(max(ps[y], 1e-12))


def rps(ps, y):
    s = 0.0
    for k in range(2):
        s += (sum(ps[:k + 1]) - (1.0 if y <= k else 0.0)) ** 2
    return s / 2.0


def ece(recs):
    bins = defaultdict(lambda: [0, 0])
    for p, y in recs:
        m = max(p)
        b = min(int(m * 10), 9)
        bins[b][0] += 1
        bins[b][1] += 1 if p.index(m) == y else 0
    n = len(recs)
    return sum(abs((c / t) - ((b + 0.5) / 10)) * (t / n)
               for b, (t, c) in bins.items())


def main():
    recs = [json.loads(l) for l in open(IN)]
    n = len(recs)
    print(f"样本: {n} 场（2026-09-01~29，五大联赛已完赛）\n")

    # ---------- 1. 1X2 校准：生产融合 vs 消融 ----------
    agg = {}
    for key, get in [("fused(生产)", lambda r: r["p"]),
                     ("model", lambda r: r["signals"]["model"]),
                     ("market", lambda r: r["signals"]["market"]),
                     ("elo", lambda r: r["signals"]["elo"])]:
        rows = [(get(r), IDX[r["actual"]]) for r in recs if get(r)]
        m = len(rows)
        agg[key] = {
            "n": m,
            "brier": sum(brier(p, y) for p, y in rows) / m,
            "logloss": sum(logloss(p, y) for p, y in rows) / m,
            "rps": sum(rps(p, y) for p, y in rows) / m,
            "dir": sum(1 for p, y in rows if p.index(max(p)) == y) / m,
            "ece": ece(rows),
        }
    print("== 1X2 校准 ==")
    print(f"{'方案':<14}{'n':>5}{'Brier':>8}{'LogLoss':>9}{'RPS':>7}{'方向':>7}{'ECE':>7}")
    for k, a in agg.items():
        print(f"{k:<14}{a['n']:>5}{a['brier']:>8.4f}{a['logloss']:>9.4f}"
              f"{a['rps']:>7.4f}{a['dir']:>6.1%}{a['ece']:>7.4f}")
    base = sum(brier([1/3]*3, IDX[r["actual"]]) for r in recs) / n
    print(f"均匀基准 Brier: {base:.4f}")

    # ---------- 2. 背离复盘 ----------
    divs = [r for r in recs if r["divergence"]]
    print(f"\n== 背离复盘（门控触发）: {len(divs)}/{n} = {len(divs)/n:.1%} ==")
    mw = sum(1 for r in divs
             if NAMES[IDX[r["actual"]]] == r["divergence"]["market_direction"])
    md = sum(1 for r in divs
             if NAMES[IDX[r["actual"]]] == r["divergence"]["model_direction"])
    both_wrong = len(divs) - mw - md
    # 注意 model/market 方向不同，不可能同时对
    print(f"市场赢 {mw} 场（{mw/len(divs):.1%}），模型赢 {md} 场（{md/len(divs):.1%}），"
          f"都没中 {both_wrong} 场" if divs else "无背离样本")
    if divs:
        print("背离明细（日期 联赛 对阵 实际 | 模型方向 市场方向 gap）:")
        for r in sorted(divs, key=lambda x: x["date"]):
            d = r["divergence"]
            mark = "市" if NAMES[IDX[r["actual"]]] == d["market_direction"] else \
                   ("模" if NAMES[IDX[r["actual"]]] == d["model_direction"] else "×")
            print(f"  [{mark}] {r['date'][5:]} {r['league']} {r['home'][:14]:<14} "
                  f"{r['score']:>4} | 模型{d['model_direction']:<5} "
                  f"市场{d['market_direction']:<5} gap{d['gap']:.2f}")

    # ---------- 3. 亚盘（收盘线）：让平诊断 ----------
    ah = [r for r in recs if r["asian_pred"] and r["ah_close"] not in (None, "")]
    res_map = {"H": "win", "D": "push", "A": "lose"}
    hits = push_hit = 0
    push_n, push_p_sum = 0, 0.0
    for r in ah:
        hg, ag = map(int, r["score"].split("-"))
        margin = hg - ag + float(r["ah_close"])
        actual = "win" if margin > 0 else ("push" if margin == 0 else "lose")
        ap = r["asian_pred"]
        pred = max(["win", "push", "lose"], key=lambda k: ap[k])
        if pred == actual:
            hits += 1
        if actual == "push":
            push_n += 1
            push_p_sum += ap["push"]
    print(f"\n== 亚盘（收盘线，n={len(ah)}）==")
    print(f"首选方向命中: {hits}/{len(ah)} = {hits/len(ah):.1%}")
    print(f"走水(push)实际 {push_n} 场（{push_n/len(ah):.1%}），"
          f"模型平均 push 概率 {push_p_sum/max(push_n,1):.1%}")
    # 公平盘口 vs 收盘线偏差
    bias = [r["asian_pred"]["line_bias"] for r in ah if r["asian_pred"]]
    print(f"收盘线 - 模型公平盘口：均值 {sum(bias)/len(bias):+.2f} 球")

    # ---------- 4. 大小球 2.5 ----------
    ou = [(r["ou_pred"]["over"], 1 if (int(r["score"].split("-")[0])
          + int(r["score"].split("-")[1])) > 2.5 else 0) for r in recs if r["ou_pred"]]
    bo = sum((p - y) ** 2 for p, y in ou) / len(ou)
    acc = sum(1 for p, y in ou if (p > 0.5) == bool(y)) / len(ou)
    print(f"\n== 大小球 2.5（n={len(ou)}）: Brier {bo:.4f}，方向 {acc:.1%} ==")

    # ---------- 5. 进球偏差 ----------
    gb = [(r["expected_goals"] - (int(r["score"].split("-")[0])
           + int(r["score"].split("-")[1]))) for r in recs if r["expected_goals"]]
    print(f"期望总进球 - 实际：均值 {sum(gb)/len(gb):+.2f}（正=高估），"
          f"MAE {sum(abs(x) for x in gb)/len(gb):.2f}")

    # ---------- 6. 半场 / 比分 ----------
    ht = [r for r in recs if r["half_time"] and r["htr"] in IDX]
    mkey = {"H": "p_home", "D": "p_draw", "A": "p_away"}
    ht_acc = sum(1 for r in ht
                 if max(("p_home", "p_draw", "p_away"),
                         key=lambda k: r["half_time"][k]) == mkey[r["htr"]]) / len(ht)
    s1 = sum(1 for r in recs if r["top_score"].get("score") == r["score"]) / n
    print(f"半场方向: {ht_acc:.1%}（n={len(ht)}）；第一比分命中: {s1:.1%}")

    # ---------- 7. 完整度分档 ----------
    print("\n== 完整度分档 ==")
    for g in ("A", "B", "C"):
        rs = [r for r in recs if r["grade"] == g]
        if not rs:
            continue
        d = sum(1 for r in rs if r["p"].index(max(r["p"])) == IDX[r["actual"]]) / len(rs)
        print(f"  {g}级: {len(rs)} 场，方向 {d:.1%}")


if __name__ == "__main__":
    main()
