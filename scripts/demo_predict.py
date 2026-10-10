"""一键演示：用内置样例比赛跑完 采集→预测 全链路（无需数据库）。

用法: python3 scripts/demo_predict.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.jingcai_predictor import predict

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _mk(pattern, venue, n=8):
    seq = pattern * ((n // len(pattern)) + 1)
    return [{"gf": gf, "ga": ga, "venue": venue} for gf, ga in seq[:n]]


STRONG_HOME = [(2, 1), (1, 0), (3, 1), (2, 0), (1, 1), (2, 2), (1, 0), (0, 1)]
MID_AWAY = [(1, 1), (0, 2), (1, 0), (2, 2), (0, 1), (1, 2), (2, 1), (1, 1)]
WEAK_AWAY = [(0, 2), (1, 3), (0, 1), (1, 1), (0, 0), (1, 2), (2, 3), (0, 1)]


def main():
    now = datetime.now(TZ)
    fixtures = [
        ("欧国联", "土耳其", "意大利",
         {"home": 2.69, "draw": 3.30, "away": 2.20}, _mk(STRONG_HOME, "H"), _mk(MID_AWAY, "A")),
        ("欧国联", "比利时", "法国",
         {"home": 3.77, "draw": 3.77, "away": 1.67}, _mk(STRONG_HOME, "H"), _mk(MID_AWAY, "A")),
    ]
    for i, (comp, h, a, odds, hr, ar) in enumerate(fixtures):
        ko = now + timedelta(days=1, hours=i * 3)
        r = predict({
            "home": h, "away": a, "competition": comp,
            "kickoff_at": ko.isoformat(), "snapshot_at": now.isoformat(),
            "league_avg_goals": 2.70,
            "home_recent": hr, "away_recent": ar,
            "odds": odds, "ou_line": 2.5,
            "asian": {"handicap": -0.5, "opening_handicap": -0.25,
                      "home_water": 0.92, "opening_home_water": 1.02},
            "elo": {"home": 1820, "away": 1740},
        })
        print("=" * 52)
        print(f"{h} vs {a}（{comp}）开球 {ko.strftime('%m-%d %H:%M')}")
        print(f"完整度 {r['grade']}({r['completeness']})  "
              f"信心 {r['confidence']}({r['confidence_score']})  "
              f"冷门风险 {r['upset_risk']}")
        print(f"λ主 {r['lambda_home']}  λ客 {r['lambda_away']}  "
              f"权重 {r['weights']}")
        print(f"主胜 {r['p_home']:.1%}  平 {r['p_draw']:.1%}  客胜 {r['p_away']:.1%}")
        d = r["derivatives"]
        ht = d["half_time"]
        print(f"半场 {ht['p_home']:.1%}/{ht['p_draw']:.1%}/{ht['p_away']:.1%}  "
              f"期望进球 {d['expected_goals']}  主要区间 {d['main_goal_interval']['label']}")
        print("比分参考:", " · ".join(f"{s['score']} {s['prob']:.1%}" for s in d["top_scores"]))
        if d["upset_score"]:
            print(f"冷门比分: {d['upset_score']['score']} {d['upset_score']['prob']:.1%}")
        print(f"BTTS {d['btts']:.1%}  大2.5 {d['over_under']['over']:.1%}", end="")
        if d["asian"]:
            a = d["asian"]
            mv = a.get("movement") or {}
            print(f"  亚盘{a['handicap']}: 赢{a['win']:.1%}  合理盘口{a['fair_handicap']}"
                  f"  变化{mv.get('direction','-')}/{mv.get('signal','-')}", end="")
        print()
        mc = r["monte_carlo"]
        print("MC:", "未运行" if not mc["ran"]
              else f"n={mc['n']} {mc['p_home']:.1%}/{mc['p_draw']:.1%}/{mc['p_away']:.1%}")
        if r["consistency_issues"]:
            print("一致性警告:", "; ".join(r["consistency_issues"]))
        if r["value"]:
            print("价值方向:", ", ".join(f"{v['outcome']} edge={v['edge']}" for v in r["value"]))
        else:
            print("价值方向: 无")
        print(f"首选: {r['recommendation']['direction']}")
    print("=" * 52)
    print("注：演示数据为示意性质，仅用于验证链路。")


if __name__ == "__main__":
    main()
