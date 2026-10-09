"""北单预测不可覆盖快照链路。

每次北单预测生成时，逐场写入一条 append-only 快照到
data/snapshots/beidan/<lottery_no>.jsonl，供日后 as-of 回测使用。

规范见 docs/beidan-snapshot-spec.md（v2）。

核心规则：
1. 快照写入后永不修改、永不删除；重跑写新记录并加 rerun_of 链。
2. generated_at/available_at 必须有真实时间证据；缺失 → observation_only=true。
3. 六玩法概率必须从同一比分矩阵聚合，每类概率和为1。
4. score_top5 仅为展示字段，不作为概率依据。
"""

import json
import os
from datetime import datetime, timezone

SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "snapshots", "beidan",
)

# 北单官方31类比分（福建体彩《中国足球彩票单场竞猜比分游戏规则》2024-04-01第六条）
# 主胜13 / 平5 / 客胜13。25类是下半场比分，不可作全场。
BEIDAN_SCORE_31 = {
    "win": ["1-0", "2-0", "2-1", "3-0", "3-1", "3-2",
            "4-0", "4-1", "4-2", "5-0", "5-1", "5-2", "胜其他"],
    "draw": ["0-0", "1-1", "2-2", "3-3", "平其他"],
    "lose": ["0-1", "0-2", "1-2", "0-3", "1-3", "2-3",
             "0-4", "1-4", "2-4", "0-5", "1-5", "2-5", "负其他"],
}

# 具体比分集合（不含"其他"），用于映射
_WIN_SCORES = set(BEIDAN_SCORE_31["win"][:-1])
_DRAW_SCORES = set(BEIDAN_SCORE_31["draw"][:-1])
_LOSE_SCORES = set(BEIDAN_SCORE_31["lose"][:-1])

HALF_FULL_9 = ["胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负"]
OU_4 = ["上单", "上双", "下单", "下双"]

# 写入统计（进程内累计，供调用方上报分母）
# 成功/失败计数，避免静默丢记录导致分母统计失真
_write_stats = {"success": 0, "failed": 0, "failed_reasons": {}}


def get_write_stats() -> dict:
    """返回写入统计快照（含失败原因分布）。"""
    return {
        "success": _write_stats["success"],
        "failed": _write_stats["failed"],
        "failed_reasons": dict(_write_stats["failed_reasons"]),
    }


def reset_write_stats():
    """重置统计（测试用）。"""
    _write_stats["success"] = 0
    _write_stats["failed"] = 0
    _write_stats["failed_reasons"] = {}


def _record_write_failure(reason: str):
    _write_stats["failed"] += 1
    _write_stats["failed_reasons"][reason] = \
        _write_stats["failed_reasons"].get(reason, 0) + 1

PROB_TOL = 1e-6


def score_to_beidan_class(h: int, a: int) -> str:
    """将具体比分 (h, a) 映射到北单官方31类。"""
    s = f"{h}-{a}"
    if h > a:
        return s if s in _WIN_SCORES else "胜其他"
    if h == a:
        return s if s in _DRAW_SCORES else "平其他"
    return s if s in _LOSE_SCORES else "负其他"


def aggregate_31class(matrix: list[list[float]]) -> dict[str, float]:
    """从全比分矩阵聚合北单官方31类分布（全精度，不舍入）。"""
    dist = {c: 0.0 for grp in BEIDAN_SCORE_31.values() for c in grp}
    n = len(matrix)
    for h in range(n):
        row = matrix[h]
        for a in range(len(row)):
            p = row[a]
            if p:
                dist[score_to_beidan_class(h, a)] += p
    return dist


# 兼容别名（旧名保留，指向31类）
def aggregate_25class(matrix):
    """已废弃：25类是下半场比分。请用 aggregate_31class。"""
    raise NotImplementedError("25类已废弃（系下半场比分），请用 aggregate_31class")


def aggregate_total_goals_8(matrix: list[list[float]]) -> dict[str, float]:
    """从矩阵聚合总进球8类：0,1,2,3,4,5,6,7+（全精度，不舍入）。"""
    dist = {str(k): 0.0 for k in range(7)}
    dist["7+"] = 0.0
    n = len(matrix)
    for h in range(n):
        for a in range(len(matrix[h])):
            p = matrix[h][a]
            if not p:
                continue
            t = h + a
            key = str(t) if t < 7 else "7+"
            dist[key] += p
    return dist


def aggregate_ou_4(matrix: list[list[float]]) -> dict[str, float]:
    """从矩阵聚合上下单双4类。上=总进球≥3，下=总进球≤2（北单官方口径，全精度）。"""
    dist = {k: 0.0 for k in OU_4}
    n = len(matrix)
    for h in range(n):
        for a in range(len(matrix[h])):
            p = matrix[h][a]
            if not p:
                continue
            t = h + a
            ou = "上" if t >= 3 else "下"
            oe = "单" if t % 2 == 1 else "双"
            dist[ou + oe] += p
    return dist


def _check_prob_sum(dist: dict, name: str) -> None:
    """概率完整性校验：和为1（容差1e-6），每项0<=p<=1且有限。"""
    import math
    for k, v in dist.items():
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"快照拒绝写入：{name}[{k}]非有限数值: {v!r}")
        if not (0.0 <= v <= 1.0):
            raise ValueError(
                f"快照拒绝写入：{name}[{k}]={v}超出[0,1]范围"
            )
    total = sum(dist.values())
    if abs(total - 1.0) > PROB_TOL:
        raise ValueError(
            f"快照拒绝写入：{name}概率和={total:.6f}，偏离1超过容差{PROB_TOL}"
        )


def build_six_play_vector(predict_result: dict) -> dict:
    """从predict()结果构建完整六玩法概率向量。

    要求 predict_result 含 score_matrix_full（model='beidan'时predictor输出）。
    P0数值一致性：wdl、p_1x2、半全场目标必须用 p_final_full（未舍入），
    与 score_matrix_full（matrix_cal，未舍入p_final校准）同源。
    p_home/p_draw/p_away 是 round(4) 展示值，不可用于快照。
    所有概率从同一矩阵聚合；score_top5仅作展示。
    """
    matrix = predict_result.get("score_matrix_full")
    if not matrix:
        raise ValueError(
            "缺少 score_matrix_full：predict() 需用 model='beidan' 调用"
        )
    deriv = predict_result.get("derivatives", {})

    # 1. 胜平负：必须用 p_final_full（未舍入），与矩阵同源
    pff = predict_result.get("p_final_full")
    if pff and len(pff) == 3:
        _p1x2_full = (float(pff[0]), float(pff[1]), float(pff[2]))
    else:
        # 回退：从矩阵边际聚合（保证与score_31一致）
        n = len(matrix)
        _ph = sum(matrix[h][a] for h in range(n) for a in range(len(matrix[h])) if h > a)
        _pd = sum(matrix[h][a] for h in range(n) for a in range(len(matrix[h])) if h == a)
        _pa = sum(matrix[h][a] for h in range(n) for a in range(len(matrix[h])) if h < a)
        _p1x2_full = (_ph, _pd, _pa)
    wdl = {
        "胜": _p1x2_full[0],
        "平": _p1x2_full[1],
        "负": _p1x2_full[2],
    }

    # 2. 让球胜平负：缺失时统一返回None，不返回零概率/None混合dict
    # 优先使用未舍入全精度（p_*_full），展示层再round；回退到舍入值
    h1x2 = deriv.get("handicap_1x2")
    if h1x2 and h1x2.get("line") is not None:
        ph_full = h1x2.get("p_home_full", h1x2["p_home"])
        pd_full = h1x2.get("p_draw_full", h1x2["p_draw"])
        pa_full = h1x2.get("p_away_full", h1x2["p_away"])
        handicap_wdl = {
            "让胜": ph_full,
            "让平": pd_full,
            "让负": pa_full,
            "handicap_line": h1x2["line"],
        }
    else:
        handicap_wdl = None

    # 3. 比分31类（完整分布，官方口径）
    score_31 = aggregate_31class(matrix)
    # score_top5 仅展示
    score_top5_display = deriv.get("top_scores", [])[:5]

    # 4. 总进球8类
    total_goals = aggregate_total_goals_8(matrix)

    # 5. 半全场9类：优先使用未舍入全精度（half_full_1x2_full）
    half_full_raw = deriv.get("half_full_1x2_full") or deriv.get("half_full_1x2") or {}
    half_full = {k: half_full_raw.get(k, 0.0) for k in HALF_FULL_9}

    # 边际对齐：半全场9项的列边际必须等于全场1X2。
    # half_full_1x2 模型本身有约1e-5的长尾截断误差，此处做列内重归一，
    # 使聚合边际精确等于校准后胜平负（IPF哲学），再做1e-6校验。
    # 但只对齐微小误差（<1e-3）： gross矛盾（如均匀1/9 vs .5/.25/.25）
    # 必须拒绝，不能静默"修复"。
    _p1x2 = _p1x2_full  # 半全场目标用全精度，与wdl/矩阵同源
    _cols = [("胜胜", "平胜", "负胜"), ("胜平", "平平", "负平"), ("胜负", "平负", "负负")]
    for _keys, _target in zip(_cols, _p1x2):
        _s = sum(half_full[k] for k in _keys)
        _dev = abs(_s - _target)
        if _dev > 1e-3:
            raise ValueError(
                f"快照拒绝写入：半全场列 {_keys} 边际 {_s:.6f} vs "
                f"全场概率 {_target:.6f}，偏离 {_dev:.2e} 超过1e-3（非浮点误差）"
            )
        if _s > 0:
            for k in _keys:
                half_full[k] = half_full[k] / _s * _target
        elif _target > 0:
            raise ValueError(
                f"快照拒绝写入：半全场列 {_keys} 全零但全场概率 {_target:.6f} 非零"
            )

    # Fix 5: 半全场对全场1X2的边际一致性校验（对齐后应精确成立）
    _marg_home = half_full["胜胜"] + half_full["平胜"] + half_full["负胜"]
    _marg_draw = half_full["胜平"] + half_full["平平"] + half_full["负平"]
    _marg_away = half_full["胜负"] + half_full["平负"] + half_full["负负"]
    for _got, _exp, _name in ((_marg_home, _p1x2[0], "全场胜"),
                              (_marg_draw, _p1x2[1], "全场平"),
                              (_marg_away, _p1x2[2], "全场负")):
        if abs(_got - _exp) > PROB_TOL:
            raise ValueError(
                f"快照拒绝写入：半全场边际不一致：{_name}边际={_got:.6f} "
                f"vs 全场概率={_exp:.6f}，偏离超过容差{PROB_TOL}"
            )

    # 6. 上下单双4类
    ou = aggregate_ou_4(matrix)

    # 完整性校验（概率和为1，容差1e-6）
    _check_prob_sum(wdl, "wdl")
    if handicap_wdl is not None:
        _check_prob_sum(
            {k: handicap_wdl[k] for k in ("让胜", "让平", "让负")},
            "handicap_wdl",
        )
    _check_prob_sum(score_31, "score_31")
    _check_prob_sum(total_goals, "total_goals")
    _check_prob_sum(half_full, "half_full")
    _check_prob_sum(ou, "ou")

    return {
        "wdl": wdl,
        "handicap_wdl": handicap_wdl,  # 缺失时为None
        "score_31": score_31,
        "score_top5_display": score_top5_display,  # 仅展示，不作概率依据
        "total_goals": total_goals,
        "half_full": half_full,
        "ou": ou,
    }


def _parse_evidence_time(value) -> "datetime | None":
    """解析时间证据：必须带时区，否则返回None（拒绝无时区，不强行按UTC）。"""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return None  # 无时区 → 拒绝，不强行解释
    return dt


def _has_real_time_evidence(value) -> bool:
    """时间证据校验：非空、合法ISO8601、带时区、不晚于当前时刻。"""
    dt = _parse_evidence_time(value)
    if dt is None:
        return False
    now = datetime.now(timezone.utc)
    # 禁止未来时间（预设时间戳的典型特征）
    if dt > now:
        return False
    return True


def _check_time_ordering(match_data: dict) -> "str | None":
    """校验时间顺序：available_at <= asof <= generated_at < kickoff。

    返回违反原因字符串，无违反返回None。
    null来源不被忽略：任一源为null即视为证据不足。
    """
    gen = _parse_evidence_time(match_data.get("generated_at"))
    kickoff = _parse_evidence_time(match_data.get("kickoff"))
    asof_raw = match_data.get("asof", match_data.get("generated_at"))
    asof = _parse_evidence_time(asof_raw)

    available_at = match_data.get("available_at") or {}
    # 空available_at = 无来源时间证据 → 直接判违规
    if not available_at:
        return "available_at为空：无来源时间证据"
    # null来源显式标记：任一源为null/无证据即不足
    for src, v in available_at.items():
        if not _has_real_time_evidence(v):
            return f"来源{src}无有效时间证据"

    if gen is None:
        return "generated_at无有效时间证据"
    if kickoff is None:
        return "kickoff无有效时间证据"
    if asof is None:
        return "asof无有效时间证据"

    # 拒绝未来时间戳（预设时间的典型特征）
    now = datetime.now(timezone.utc)
    if gen > now:
        return "generated_at为未来时间（预设时间戳）"
    if asof > now:
        return "asof为未来时间"

    for src, v in available_at.items():
        src_dt = _parse_evidence_time(v)
        if src_dt > gen:
            return f"来源{src}的available_at晚于generated_at"
        if src_dt > asof:
            return f"来源{src}的available_at晚于asof"
    if asof > gen:
        return "asof晚于generated_at"
    if not (gen < kickoff):
        return "generated_at不早于kickoff（非赛前快照）"
    return None


def _existing_seqs(path: str, lottery_no: str) -> dict[str, int]:
    """读取已有快照，返回 {seq: 最新记录序号}，用于 rerun_of 链。"""
    seen: dict[str, int] = {}
    if not os.path.exists(path):
        return seen
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("lottery_no") == lottery_no:
                seq = str(rec.get("seq", ""))
                seen[seq] = seen.get(seq, 0) + 1
    return seen


def write_snapshot(match_data: dict, predict_result: dict) -> dict:
    """写入一条北单预测快照（append-only）。带写入统计。

    match_data 需含：lottery_no, seq, league, home, away, kickoff,
        generated_at（真实生成时刻）, available_at（各源可用时间dict，可选）,
        handicap_line（可选）, sp_snapshot（可选，须带采集时间）,
        data_completeness（可选）, skipped/skip_reason（可选）。
    predict_result 为 predict(payload, model='beidan') 的返回。

    返回写入的记录（含 snapshot_id / rerun_of / observation_only）。
    时间证据缺失时自动标 observation_only=true。
    写入失败时计入 _write_stats（不静默丢），并重新抛出。
    """
    try:
        record = _write_snapshot_impl(match_data, predict_result)
    except Exception as e:
        _record_write_failure(f"{type(e).__name__}:{str(e)[:80]}")
        raise
    _write_stats["success"] += 1
    return record


def _write_snapshot_impl(match_data: dict, predict_result: dict) -> dict:
    """write_snapshot 的实际实现（统计由外层 wrapper 负责）。"""
    lottery_no = str(match_data["lottery_no"])
    seq = str(match_data["seq"])

    # 时间证据校验：顺序 available_at <= asof <= generated_at < kickoff
    time_violation = _check_time_ordering(match_data)
    observation_only = time_violation is not None
    observation_reasons = [time_violation] if time_violation else []

    # Fix 4: 合成样本强制 observation_only=true
    if match_data.get("synthetic_sample"):
        observation_only = True
        observation_reasons.append("synthetic_sample=true：合成样本，防误入回测")

    # Fix 1 (revised P0): SP时间证据校验
    # 漏洞修复：原检查只在 collected_at 为真时触发；现改为：
    # 只要 sp_wdl 有数值（参与了预测），就必须有SP源的 available_at 真实证据，
    # 无论 collected_at 是否为 null。无证据 → observation_only=true。
    # 新增：collected_at 若存在，必须与对应SP源的 available_at 一致（容差5分钟）
    # 且 <= asof，且该源确实是赔率输入来源。
    sp_snap = match_data.get("sp_snapshot")
    if sp_snap and isinstance(sp_snap, dict):
        sp_wdl = sp_snap.get("sp_wdl") or {}
        has_sp_values = bool(sp_wdl) and any(
            v is not None for v in sp_wdl.values()
        ) if isinstance(sp_wdl, dict) else bool(sp_wdl)
        if has_sp_values:
            sp_evidence = False
            sp_src_name = None
            sp_src_time = None
            available_at = match_data.get("available_at") or {}
            for src, v in available_at.items():
                # 精确匹配SP/赔率源：避免"espn"误命中"sp"子串
                # 匹配规则：源名含独立token "sp"/"odds"/"odd"（如下划线/连字符分隔），
                # 或以 "sp"/"odds" 开头/结尾的复合名（如 okoo_sp, sp_source）
                s = src.lower().replace("-", "_")
                tokens = s.split("_")
                is_sp_src = (
                    "sp" in tokens or "odds" in tokens or "odd" in tokens
                    or s.startswith("sp_") or s.endswith("_sp")
                    or s.startswith("odds_") or s.endswith("_odds")
                    or s == "sp" or s == "odds"
                )
                if is_sp_src:
                    if _has_real_time_evidence(v):
                        sp_evidence = True
                        sp_src_name = src
                        sp_src_time = v
                        break
            if not sp_evidence:
                observation_only = True
                observation_reasons.append(
                    "sp_wdl数值参与预测但无SP源available_at真实采集证据"
                    "（collected_at=%s；不准用t_data_ready/战绩时间替代）"
                    % sp_snap.get("collected_at")
                )
                # 无证据时 collected_at 置 null，不保留冒充值
                sp_snap = dict(sp_snap)
                sp_snap["collected_at"] = None
                match_data = dict(match_data)
                match_data["sp_snapshot"] = sp_snap
            else:
                # collected_at 若存在，必须与SP源的available_at对应
                sp_collected = sp_snap.get("collected_at")
                if sp_collected is not None:
                    c_dt = _parse_evidence_time(sp_collected)
                    s_dt = _parse_evidence_time(sp_src_time)
                    asof_raw = match_data.get("asof", match_data.get("generated_at"))
                    a_dt = _parse_evidence_time(asof_raw)
                    _mismatch = False
                    _reason = ""
                    if c_dt is None:
                        _mismatch, _reason = True, "collected_at时间格式无效"
                    elif s_dt is None:
                        _mismatch, _reason = True, "SP源available_at时间格式无效"
                    elif abs((c_dt - s_dt).total_seconds()) > 300:
                        _mismatch, _reason = True, (
                            f"collected_at({sp_collected})与"
                            f"available_at[{sp_src_name}]({sp_src_time})不一致"
                            f"（差{abs((c_dt - s_dt).total_seconds()):.0f}s>300s）"
                        )
                    elif a_dt is not None and c_dt > a_dt:
                        _mismatch, _reason = True, (
                            f"collected_at({sp_collected})晚于asof({asof_raw})"
                        )
                    if _mismatch:
                        observation_only = True
                        observation_reasons.append(
                            f"SP时间证据不对应：{_reason}"
                        )

    # 来源门控：让球线参与预测必须有独立采集证据
    # runner的 hc=m.get("handicap") 直接进 payload.handicap_line，
    # 但 available_at 只记战绩源时间。无让球源证据 → observation_only=true。
    # 禁止用战绩采集时间/文件mtime替代。
    hc_line = match_data.get("handicap_line")
    if hc_line is not None:
        hc_evidence = False
        available_at = match_data.get("available_at") or {}
        for src, v in available_at.items():
            s = src.lower().replace("-", "_")
            tokens = s.split("_")
            is_hc_src = (
                "handicap" in tokens or "rangqiu" in tokens
                or "rq" in tokens
                or s.startswith("handicap_") or s.endswith("_handicap")
                or s == "handicap"
            )
            if is_hc_src and _has_real_time_evidence(v):
                hc_evidence = True
                break
        if not hc_evidence:
            observation_only = True
            observation_reasons.append(
                "handicap_line=%s参与预测但无让球源available_at真实采集证据"
                "（不准用战绩采集时间/文件mtime替代）" % hc_line
            )
    # 注：handicap_line=None 时不触发门控。predictor内 upset_risk 用0是既有模型行为，
    # 属诊断字段默认值，不影响六玩法核心概率；快照中 handicap_wdl 保持 None。

    six = build_six_play_vector(predict_result)

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"{lottery_no}.jsonl")

    # rerun_of 初值（锁内会重算修正，防并发下预读过期）
    # 第一条 snapshot_id="{lottery_no}:{seq}"（无后缀）
    rerun_of = None
    snapshot_id = f"{lottery_no}:{seq}"

    generated_at = match_data.get("generated_at")
    record = {
        "snapshot_id": snapshot_id,
        "lottery_no": lottery_no,
        "seq": seq,
        "rerun_of": rerun_of,
        "league": match_data.get("league"),
        "home": match_data.get("home"),
        "away": match_data.get("away"),
        "kickoff": match_data.get("kickoff"),
        "generated_at": generated_at,
        "available_at": match_data.get("available_at") or {},
        "asof": match_data.get("asof", generated_at),
        # P0数值一致性：lambda用全精度（lambda_*_full），回退到round(3)展示值并标注
        "lambda_home": predict_result.get("lambda_home_full",
                                         predict_result.get("lambda_home")),
        "lambda_away": predict_result.get("lambda_away_full",
                                         predict_result.get("lambda_away")),
        "lambda_is_display_rounded": "lambda_home_full" not in predict_result,
        # p_1x2 与 six_play_vector.wdl 同源（p_final_full未舍入）
        "p_1x2": [six["wdl"]["胜"], six["wdl"]["平"], six["wdl"]["负"]],
        "six_play_vector": six,
        "score_matrix_full": predict_result.get("score_matrix_full"),
        "model_version": predict_result.get("model_version"),
        "handicap_line": match_data.get("handicap_line"),
        "sp_snapshot": match_data.get("sp_snapshot"),
        "data_completeness": match_data.get("data_completeness"),
        "skipped": bool(match_data.get("skipped", False)),
        "skip_reason": match_data.get("skip_reason"),
        "observation_only": observation_only,
        "observation_reason": "; ".join(observation_reasons) if observation_reasons else None,
        "provenance_unverified": observation_only,
        "synthetic_sample": bool(match_data.get("synthetic_sample", False)),
    }

    # 并发安全写入：O_APPEND + fcntl独占锁 + fsync
    # 旧记录路径/字节不变，只追加。锁保护下读-算rerun_of-追加是原子的。
    import fcntl
    line = json.dumps(record, ensure_ascii=False) + "\n"
    # 注意：rerun_of 需要在锁内重新计算（上面的预读可能已过期）
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # 锁内重读，确定最新的 rerun_of（防并发下预读过期）
        os.lseek(fd, 0, os.SEEK_SET)
        existing_ids = []
        with os.fdopen(os.dup(fd), "r", encoding="utf-8", closefd=False) as rf:
            for rline in rf:
                rline = rline.strip()
                if not rline:
                    continue
                try:
                    rec = json.loads(rline)
                except json.JSONDecodeError:
                    continue
                if rec.get("lottery_no") == lottery_no and str(rec.get("seq")) == seq:
                    existing_ids.append(rec.get("snapshot_id", ""))
        if existing_ids:
            # 锁内修正 rerun_of / snapshot_id（覆盖锁外预读的值）
            record["rerun_of"] = existing_ids[-1]
            record["snapshot_id"] = f"{lottery_no}:{seq}#r{len(existing_ids) + 1}"
            line = json.dumps(record, ensure_ascii=False) + "\n"
        os.lseek(fd, 0, os.SEEK_END)
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)

    return record
