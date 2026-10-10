"""参数敏感性分析：在演示比赛上扫描关键参数，看 1X2 输出的波动范围。

目的不是调准（没有真实赛果调不准），而是确认模型对参数选择是鲁棒的：
某个参数小幅变化不应导致预测方向翻转，否则说明模型不稳定、不可信。

用法: python3 scripts/sensitivity.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.jingcai_predictor import predict

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def _mk(pattern, venue):
    return [{"gf": gf, "ga": ga, "venue": venue} for gf, ga in pattern]


def base_payload():
    now = datetime.now(TZ)
    return {
        "home": "阿森纳", "away": "切尔西", "competition": "英超",
        "kickoff_at": (now + timedelta(days=3)).isoformat(),
        "snapshot_at": now.isoformat(),
        "league_avg_goals": 2.70,
        "home_recent": _mk([(2, 1), (1, 0), (3, 1), (2, 0), (1, 1), (2, 2), (1, 0), (0, 1)], "H"),
        "away_recent": _mk([(1, 1), (0, 2), (1, 0), (2, 2), (0, 1), (1, 2), (2, 1), (1, 1)], "A"),
        "odds": {"home": 1.95, "draw": 3.60, "away": 4.20},
        "ou_line": 2.5,
    }


GRID = {
    "rho（Dixon-Coles 相关）": ("rho", [-0.20, -0.13, -0.05, 0.0]),
    "decay（时间衰减）": ("decay", [0.80, 0.85, 0.90, 0.95]),
    "ht_factor（半场进球占比）": ("ht_factor", [0.40, 0.44, 0.48]),
}


def main():
    payload = base_payload()
    base = predict(payload)
    print(f"基准: 主胜 {base['p_home']:.1%} 平 {base['p_draw']:.1%} 客胜 {base['p_away']:.1%}")
    print("-" * 64)
    for label, (key, values) in GRID.items():
        rows = []
        for v in values:
            r = predict(payload, config={key: v})
            rows.append((v, r["p_home"], r["p_draw"], r["p_away"],
                         r["recommendation"]["direction"]))
        homes = [x[1] for x in rows]
        spread = max(homes) - min(homes)
        dirs = {x[4] for x in rows}
        flag = "✓ 稳定" if len(dirs) == 1 and spread < 0.08 else "⚠ 敏感"
        print(f"{label}: 主胜波动 {min(homes):.1%}~{max(homes):.1%} "
              f"(极差 {spread:.1%}) 方向一致: {len(dirs) == 1}  {flag}")
        for v, ph, pd, pa, d in rows:
            print(f"    {key}={v}: {ph:.1%} / {pd:.1%} / {pa:.1%}  首选{d}")
    print("-" * 64)
    print("注：波动小=鲁棒；若某参数导致方向翻转，生产环境应按联赛拟合该参数。")


if __name__ == "__main__":
    main()
