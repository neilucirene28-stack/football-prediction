"""北单模型调用示例。"""
from engine import predict

payload = {
    "home": "巴西国际", "away": "科林蒂安",
    "kickoff_at": "2026-10-08T06:00:00+08:00",
    "snapshot_at": "2026-10-06T12:00:00+08:00",
    "competition": "巴西甲",
    "handicap": 0,
    "odds": {"home": 1.85, "draw": 3.4, "away": 4.2},
    "home_recent": [{"gf": 2, "ga": 1, "venue": "home", "date": "2026-10-01"}] * 6,
    "away_recent": [{"gf": 1, "ga": 1, "venue": "away", "date": "2026-10-01"}] * 6,
}

# 竞彩模式（默认）
r_jc = predict(payload, model="jingcai")
print("竞彩模式:")
print(f"  model={r_jc['model']}, beidan字段={r_jc['beidan']}")
print(f"  胜平负: {r_jc['p_home']:.3f}/{r_jc['p_draw']:.3f}/{r_jc['p_away']:.3f}")

# 北单模式
r_bd = predict(payload, model="beidan")
print("\n北单模式:")
print(f"  model={r_bd['model']}")
b = r_bd["beidan"]
print(f"  校准后胜平负首选概率: {b['calibrated_p_top_sp']}")
print(f"  校准后让球首选概率: {b['calibrated_p_top_rq']}")
print(f"  翻车风险: {b['upset_risk']} ({b['upset_risk_tier']}风险)")
print(f"  玩法: {'/'.join(b['playtypes'])}")
