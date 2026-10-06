"""阵容因子使用纪律（v2.5）。

纪律（由 2026-10-05 七场复盘确立）：
- 默认：阵容变化只调整比分分布的离散度（冷门尾部加厚），不直接下调 λ。
- 仅当缺阵者为该队射手榜前二 且 无对位替代时，才下调该队 λ 10~15%。
- 正例：007 波兰无莱万（队内头号射手、无对位替代）→ 波兰 0 进球，λ 下调成立。
- 反例：002 法国无姆巴佩（轮换幅度大但整体有替代）→ 法国仍 4:1，不该砍 λ。

状态：模块已实现并通过单元测试。但历史回填数据中 0/572 场带有阵容标注，
无法做 walk-forward 验证，按"先验证再进生产"纪律，暂不接入 predict() 生产链路。
待积累带阵容标注的历史数据后，再验证并接入。

输入的缺阵标注格式（未来数据链路提供时）：
    {"team": "home"|"away", "player": str,
     "is_top2_scorer": bool,   # 是否为该队射手榜前二
     "has_replacement": bool}  # 是否有对位替代者
"""

# 合格缺阵（射手榜前二 + 无替代）的 λ 下调幅度：10~15%，默认取中 12.5%。
_TOP_SCORER_CUT = 0.875

# 非合格缺阵的离散度调整：比分矩阵冷门尾部加厚幅度（相对）。
_DISPERSION_BUMP = 0.10


def classify_absence(absence: dict) -> str:
    """把单条缺阵分类为 'cut_lambda' / 'dispersion_only' / 'ignore'。

    - cut_lambda：射手榜前二 且 无对位替代 → 可下调 λ
    - dispersion_only：其他已确认缺阵 → 只调离散度
    - ignore：信息不足（缺关键字段）→ 忽略，不做任何调整
    """
    if not isinstance(absence, dict):
        return "ignore"
    if absence.get("is_top2_scorer") is True and \
            absence.get("has_replacement") is False:
        return "cut_lambda"
    if absence.get("player"):
        return "dispersion_only"
    return "ignore"


def lineup_adjustment(absences: list[dict] | None) -> dict:
    """输入缺阵列表，返回纪律化的调整方案。

    返回：
      {"home_lambda_mult": float, "away_lambda_mult": float,
       "dispersion_bump": float,   # 0 = 不调，>0 = 冷门尾部加厚比例
       "qualifying": [...],        # 触发 λ 下调的缺阵
       "notes": [...]}
    """
    out = {"home_lambda_mult": 1.0, "away_lambda_mult": 1.0,
           "dispersion_bump": 0.0, "qualifying": [], "notes": []}
    if not absences:
        return out
    for ab in absences:
        kind = classify_absence(ab)
        team = ab.get("team")
        if kind == "cut_lambda" and team in ("home", "away"):
            key = f"{team}_lambda_mult"
            out[key] *= _TOP_SCORER_CUT
            out["qualifying"].append(ab.get("player"))
            out["notes"].append(
                f"{ab.get('player')}（射手榜前二、无替代）：{team} λ ×{_TOP_SCORER_CUT}")
        elif kind == "dispersion_only":
            out["dispersion_bump"] = _DISPERSION_BUMP
            out["notes"].append(
                f"{ab.get('player')}：仅加厚冷门尾部，不调 λ")
        # ignore → 静默跳过，不编造调整
    out["home_lambda_mult"] = round(out["home_lambda_mult"], 4)
    out["away_lambda_mult"] = round(out["away_lambda_mult"], 4)
    return out
