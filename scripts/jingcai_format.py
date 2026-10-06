#!/usr/bin/env python3
"""竞彩官方玩法输出格式（2026-10-06 定版，用户 09:32 细化确认）。

五大玩法：①胜平负 ②让球胜平负 ③比分（首选加粗标记）
④进球数（保持原来的"期望+最可能区间"格式）
⑤半全场胜平负（胜胜/胜平/胜负/平胜/平平/平负/负胜/负平/负负 9 种组合）。
冷门比分保留为补充分析行。

本模块是"稳胆排序"报告的唯一格式依据：给定 predict() 的结果 dict，
输出每场的玩法行。稳胆三档排序、让平纪律标记、分歧标记、
亚盘/天气/阵容等依据行由调用方负责，本模块只管玩法行的格式。

报告内容纪律（用户 2026-10-06 09:40 立规矩）：
- 依据行不许罗列历史比分：H2H 的比分枚举（如"3-2、2-2、4-1"）、
  近期战绩的具体比分、"一周前某比分"等备注一律不写。
- H2H 如需体现，只留方向性一句话（用本模块 format_h2h_brief），
  不带任何比分数字。
- 战绩统计数字可以留（如"近10场6胜"），比分罗列去掉。
- 只给当前分析和预测结果：五大玩法、赔率变化、天气/阵容、
  纪律/分歧标记、冷门比分。

用法：
    from scripts.jingcai_format import format_five_playtypes
    lines = format_five_playtypes(pred)  # pred = predict(payload) 的返回
    for line in lines: print(line)
"""

HALF_FULL_ORDER = ("胜胜", "胜平", "胜负",
                   "平胜", "平平", "平负",
                   "负胜", "负平", "负负")


def _top_n(prob_dict: dict, n: int = 3) -> list[tuple[str, float]]:
    """按概率降序取前 n 项（概率相同按 key 排序，保证确定性）。"""
    return sorted(prob_dict.items(), key=lambda kv: (-kv[1], kv[0]))[:n]


def format_1x2(pred: dict) -> str:
    """①胜平负。"""
    return (f"胜平负：胜({pred['p_home']:.3f})/平({pred['p_draw']:.3f})"
            f"/负({pred['p_away']:.3f})")


def format_handicap_1x2(pred: dict) -> str:
    """②让球胜平负（用校准后的让平建模概率）。"""
    h = pred["derivatives"].get("handicap_1x2")
    if not h:
        return "让球胜平负：无让球数据"
    line = h["line"]
    rq = f"({line:+d})" if isinstance(line, int) else f"({line})"
    return (f"让球胜平负{rq}：让胜({h['p_home']:.3f})/让平({h['p_draw']:.3f})"
            f"/让负({h['p_away']:.3f})")


def format_top_scores(pred: dict, n: int = 3) -> str:
    """③比分：概率最高的 n 个，首选（概率最高）加粗标记。"""
    scores = pred["derivatives"]["top_scores"][:n]
    parts = []
    for idx, s in enumerate(scores):
        label = f"**{s['score']}**" if idx == 0 else s["score"]
        parts.append(f"{label}({s['prob']:.3f})")
    return "比分：" + "、".join(parts)


def format_total_goals(pred: dict) -> str:
    """④进球数：保持原来的"期望+最可能区间"格式（用户 2026-10-06 09:32 确认不动）。

    注：引擎同时输出 total_goals_exact（0~7+ 精确分布）供后续调用，
    但报告行按用户要求沿用区间格式。
    """
    d = pred["derivatives"]
    interval = d["main_goal_interval"]  # {"label": "2-3球", "prob": 0.485}
    exp = d["expected_goals"]
    return f"进球数：{interval['label']}({interval['prob']:.3f})，期望{exp}球"


def format_half_full(pred: dict, n: int = 3) -> str:
    """⑤半全场胜平负：概率最高的 n 种组合。"""
    d = pred["derivatives"]["half_full_1x2"]
    top = _top_n({k: d[k] for k in HALF_FULL_ORDER if k in d}, n)
    inner = "、".join(f"{k}({p:.3f})" for k, p in top)
    return f"半全场胜平负：{inner}"


def format_upset_score(pred: dict) -> str:
    """冷门比分（补充分析行）。"""
    u = pred["derivatives"].get("upset_score")
    if not u:
        return "冷门比分：无"
    return f"冷门比分：{u['score']}({u['prob']:.3f})"


def format_h2h_brief(home: str, away: str,
                     home_wins: int, draws: int, away_wins: int) -> str:
    """H2H 方向一句话（2026-10-06 09:40 精简：只留方向，不带任何比分数字）。

    依据行调用此函数，禁止手写比分枚举。统计数字（近N场）可留，
    比分罗列一律去掉。
    如：format_h2h_brief("法国", "比利时", 4, 0, 0) -> "H2H（近4场）：法国占优"
    """
    total = home_wins + draws + away_wins
    if total <= 0:
        return "H2H：无交锋记录"
    if home_wins > away_wins and home_wins > draws:
        verdict = f"{home}占优"
    elif away_wins > home_wins and away_wins > draws:
        verdict = f"{away}占优"
    else:
        verdict = "均势"
    return f"H2H（近{total}场）：{verdict}"


def format_five_playtypes(pred: dict) -> list[str]:
    """返回五大玩法行 + 冷门比分补充行（共 6 行）。"""
    return [
        format_1x2(pred),
        format_handicap_1x2(pred),
        format_top_scores(pred),
        format_total_goals(pred),
        format_half_full(pred),
        format_upset_score(pred),
    ]


if __name__ == "__main__":
    # 自检：用一场示例比赛跑通全流程
    import sys
    from datetime import datetime, timedelta
    sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
    from engine.predictor import predict

    def mk(gf, ga, venue):
        return [{"gf": gf, "ga": ga, "venue": venue} for _ in range(8)]

    now = datetime.now().astimezone()
    payload = {
        "home": "法国", "away": "比利时", "competition": "欧国联",
        "kickoff_at": (now + timedelta(days=2)).isoformat(),
        "snapshot_at": (now + timedelta(days=1)).isoformat(),
        "league_avg_goals": 2.70,
        "home_recent": mk(2, 1, "H"),
        "away_recent": mk(1, 1, "A"),
        "odds": {"home": 1.48, "draw": 4.70, "away": 5.90},
        "handicap_line": -1,
        "ou_line": 2.5,
    }
    pred = predict(payload)
    assert pred["status"] == "ok", pred
    for line in format_five_playtypes(pred):
        print(line)
    # 格式断言
    d = pred["derivatives"]
    assert abs(sum(d["half_full_1x2"].values()) - 1.0) < 0.01
    assert abs(sum(d["total_goals_exact"].values()) - 1.0) < 0.01
    print("OK: 五大玩法格式自检通过")
