"""北单 Adjusted Goals（engine/beidan_adjusted_goals.py）

垃圾时间进球贬值（参考 tanjt107/football-prediction）：
- 70分钟后，领先方的进球线性贬值
- 90分钟的进球只算 0.5 球
- 落后方/扳平球不贬值（仍有竞技价值）

作用位置：Elo更新和λ估计的数据输入层（调用方在喂比分前先调 adjust_match）。

状态说明（诚实记录）：
- 贬值逻辑已实现并有测试。
- 离线实验（对比贬值前后 walk-forward Brier）暂未运行：26101期历史
  记录只有终场比分，无进球时间分钟数据，无法做贬值。待有分钟级
  数据源后再跑实验，有效果才进生产。
- 当前默认关闭，调用方需显式启用。

用法：
    from engine.beidan_adjusted_goals import adjust_goals, adjust_match
    # 单场：goals=[(分钟, 是否主队进球)], 返回贬值后的 (主队进球, 客队进球)
    adjust_goals([(75, True), (88, False)], home_leading=True)
"""

from __future__ import annotations


def goal_weight(minute: int, scorer_leading: bool) -> float:
    """单个进球的权重。

    minute: 进球分钟（1-90+）
    scorer_leading: 进球方在进球时是否领先
    返回权重 0.5-1.0。
    """
    if not scorer_leading:
        return 1.0
    if minute <= 70:
        return 1.0
    # 70'→1.0，90'→0.5，线性
    w = 1.0 - (minute - 70) / 40.0
    return round(max(w, 0.5), 4)


def adjust_goals(home_goals: list[int], away_goals: list[int]) -> tuple[float, float]:
    """输入两队进球分钟列表，返回贬值后的 (主队有效进球, 客队有效进球)。

    领先判断：按时间顺序回放比分，进球时领先的一方才可能被贬值。
    """
    events = [(m, True) for m in home_goals] + [(m, False) for m in away_goals]
    events.sort(key=lambda e: e[0])
    h, a = 0, 0
    h_adj, a_adj = 0.0, 0.0
    for minute, is_home in events:
        if is_home:
            leading = h > a
            h += 1
            h_adj += goal_weight(minute, leading)
        else:
            leading = a > h
            a += 1
            a_adj += goal_weight(minute, leading)
    return round(h_adj, 4), round(a_adj, 4)


def adjust_match(score_h: int, score_a: int,
                 home_minutes: list[int] | None = None,
                 away_minutes: list[int] | None = None) -> tuple[float, float]:
    """便捷封装：有分钟数据时贬值，无分钟数据时原样返回。

    无分钟数据时返回原比分（不编造时间分布）。
    """
    if home_minutes is None or away_minutes is None:
        return float(score_h), float(score_a)
    if len(home_minutes) != score_h or len(away_minutes) != score_a:
        # 分钟数与比分对不上，拒绝贬值，原样返回
        return float(score_h), float(score_a)
    return adjust_goals(home_minutes, away_minutes)


# 开关：默认关闭，实验验证有效后才由调用方开启
ENABLED = False
