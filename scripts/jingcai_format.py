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


def format_team_intelligence(pred: dict) -> list[str]:
    """独立情报依据行；不改变五玩法、概率或稳胆排序。"""
    observations = pred.get('team_observations') or {}
    if observations.get('status') in ('ok', 'partial'):
        return format_source_observations(pred)
    intel=pred.get("team_intelligence") or {}
    if intel.get("status") not in ("ok", "partial"):
        return ["球队情报：外援注册/阵容数据未知，未作概率修正。"]
    lines=[]
    for side,label in (("home","主队"),("away","客队")):
        team=intel.get("teams",{}).get(side)
        if team is None:
            lines.append(label+"情报：未知，不能推断无外援或无缺阵。");continue
        status={"expected":"预计首发","confirmed":"确认首发","unknown":"首发未知"}[team["lineup_status"]]
        foreign=team["foreign_starters_exact"]
        count=(str(foreign)+"人" if foreign is not None else
               f"已知{team['foreign_starters_known']}人（名单或注册身份不完整）")
        absent="、".join(p["name"] for p in team["confirmed_unavailable"]) or "未记录"
        lines.append(f"{label}情报：{status}，外援首发{count}；确认缺阵{absent}；影响系数未启用。")
    return lines


def format_source_observations(pred: dict) -> list[str]:
    obs = pred.get('team_observations') or {}
    lines = []
    for side, label in (('home', '主队'), ('away', '客队')):
        t = obs.get('selected_by_side', {}).get(side)
        if t is None:
            lines.append(label + '情报：未知，不能推断无伤停、无外援或零牌数。'); continue
        f = t['features']
        state = {'confirmed': '确认首发', 'expected': '预计首发', 'unknown': '首发未知'}[t['lineup_status']]
        shown = lambda v: str(v) if v is not None else '未知'
        absent = '、'.join(p['name'] for p in t['confirmed_unavailable']) or '未记录（不能推断无缺阵）'
        lines.append(f"{label}情报（{t['source']}）：{state}；确认缺阵{absent}，出战成疑{t['doubtful_n']}条；外援首发{shown(f['foreign_starters_exact'])}；当前名单赛季黄/红牌{shown(f['season_yellow_total'])}/{shown(f['season_red_total'])}；概率系数未启用。")
        freshness = t.get('freshness') or {}
        if freshness.get('oldest_collection_age_seconds') is not None:
            lines.append(f"{label}来源采集距快照约{freshness['newest_collection_age_seconds'] / 60:.1f}～{freshness['oldest_collection_age_seconds'] / 60:.1f}分钟；采集时间不能证明原文已更新。")
        for kind, kind_label in (('lineup', '阵容'), ('discipline', '牌数'), ('registration', '注册')):
            other = obs.get('selected_by_dimension', {}).get(side, {}).get(kind)
            if other and other['source'] != t['source']:
                detail = {'lineup': {'confirmed': '确认首发', 'expected': '预计首发', 'unknown': '首发未知'}[other['lineup_status']],
                          'discipline': '已观测黄/红牌汇总' + shown(other['features']['observed_season_yellow_sum']) + '/' + shown(other['features']['observed_season_red_sum']) + '（完整性另见特征）',
                          'registration': '按赛事赛季注册证据核验'}[kind]
                lines.append(f"{label}{kind_label}补充（{other['source']}）：{detail}；独立名单，未按姓名合并。")
    failed = [r['source'] + ':' + r['status'] for r in obs.get('sources', []) if r['status'] != 'ok']
    if failed: lines.append('情报来源状态：' + '、'.join(failed))
    if obs.get('conflicts'): lines.append('情报存在未解决的同源多包冲突，相关来源未选入。')
    return lines


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
    text = (f"让球胜平负{rq}：让胜({h['p_home']:.3f})/让平({h['p_draw']:.3f})"
            f"/让负({h['p_away']:.3f})")
    view = h.get("risk_view")
    if view:
        names = {"home": "让胜", "draw": "让平", "away": "让负"}
        pair = "＋".join(names[k] for k in view["top2"])
        text += (f"；首选{names[view['top1']]}"
                 f"；让平={view['draw_interpretation']}（{view['draw_probability']:.1%}）"
                 f"；Top2 {pair}覆盖{view['top2_coverage']:.1%}"
                 f"，遗漏{names[view['top2_excluded']]}"
                 f"{view['top2_excluded_probability']:.1%}")
    return text


def format_top_scores(pred: dict, n: int = 5) -> str:
    """③比分：概率最高的 n 个（v2.10：从3改成5，分布呈现），首选加粗标记。"""
    scores = pred["derivatives"]["top_scores"][:n]
    parts = []
    for idx, s in enumerate(scores):
        label = f"**{s['score']}**" if idx == 0 else s["score"]
        parts.append(f"{label}({s['prob']:.3f})")
    text = "比分：" + "、".join(parts)
    explanation = pred["derivatives"].get("score_explanation")
    if explanation:
        names = {"home": "主胜", "draw": "平局", "away": "客胜"}
        aligned = explanation["scores_within_wdl_direction"]
        text += "\n胜平负首选：" + names[explanation["wdl_direction"]]
        text += "；该方向内比分参考：" + "、".join(
            f"{s['score']}({s['prob']:.1%})" for s in aligned[:n])
        text += "（均为原始单比分概率，未重新归一）"
        if explanation["global_mode_direction_differs"]:
            text += "\n单个最可能比分与汇总胜平负方向不同可同时成立。"
    return text


def format_goal_intervals(pred: dict) -> str:
    """总进球三档区间：0-1球 / 2-3球 / 4+球（v2.10新增，分布呈现）。"""
    d = pred["derivatives"].get("goal_interval_probs") or {}
    p01 = d.get("0-1球", 0)
    p23 = d.get("2-3球", 0)
    p4p = d.get("4+球", 0)
    return f"进球区间：0-1球({p01:.3f})、2-3球({p23:.3f})、4+球({p4p:.3f})"


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
    """返回五大玩法行 + 进球区间行 + 冷门比分补充行（共 7 行，v2.10）。"""
    return [
        format_1x2(pred),
        format_handicap_1x2(pred),
        format_top_scores(pred),
        format_total_goals(pred),
        format_goal_intervals(pred),
        format_half_full(pred),
        format_upset_score(pred),
    ]


if __name__ == "__main__":
    # 自检：用一场示例比赛跑通全流程
    import sys
    from datetime import datetime, timedelta
    sys.path.insert(0, "/home/hatch/workspace/football-prediction-v2")
    from engine.jingcai_predictor import predict

    def mk(gf, ga, venue):
        return [{"gf": gf, "ga": ga, "venue": venue} for _ in range(8)]

    now = datetime.now().astimezone()
    payload = {
        "home": "法国", "away": "比利时", "competition": "欧国联",
        "kickoff_at": (now + timedelta(days=2)).isoformat(),
        "snapshot_at": now.isoformat(),
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
