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

# 北单官方25类比分（以官方口径为准）
BEIDAN_SCORE_25 = {
    "win": ["1-0", "2-0", "2-1", "3-0", "3-1", "3-2",
            "4-0", "4-1", "4-2", "5-0", "5-1", "5-2", "胜其他"],
    "draw": ["0-0", "1-1", "2-2", "3-3", "平其他"],
    "lose": ["0-1", "0-2", "1-2", "0-3", "1-3", "2-3", "负其他"],
}

# 具体比分集合（不含"其他"），用于映射
_WIN_SCORES = set(BEIDAN_SCORE_25["win"][:-1])
_DRAW_SCORES = set(BEIDAN_SCORE_25["draw"][:-1])
_LOSE_SCORES = set(BEIDAN_SCORE_25["lose"][:-1])

HALF_FULL_9 = ["胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负"]
OU_4 = ["上单", "上双", "下单", "下双"]

PROB_TOL = 1e-6


def score_to_beidan_class(h: int, a: int) -> str:
    """将具体比分 (h, a) 映射到北单官方25类。"""
    s = f"{h}-{a}"
    if h > a:
        return s if s in _WIN_SCORES else "胜其他"
    if h == a:
        return s if s in _DRAW_SCORES else "平其他"
    return s if s in _LOSE_SCORES else "负其他"


def aggregate_25class(matrix: list[list[float]]) -> dict[str, float]:
    """从全比分矩阵聚合北单官方25类分布。"""
    dist = {c: 0.0 for grp in BEIDAN_SCORE_25.values() for c in grp}
    n = len(matrix)
    for h in range(n):
        row = matrix[h]
        for a in range(len(row)):
            p = row[a]
            if p:
                dist[score_to_beidan_class(h, a)] += p
    return {k: round(v, 6) for k, v in dist.items()}


def aggregate_total_goals_8(matrix: list[list[float]]) -> dict[str, float]:
    """从矩阵聚合总进球8类：0,1,2,3,4,5,6,7+。"""
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
    return {k: round(v, 6) for k, v in dist.items()}


def aggregate_ou_4(matrix: list[list[float]]) -> dict[str, float]:
    """从矩阵聚合上下单双4类。上=总进球≥3，下=总进球≤2（北单官方口径）。"""
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
    return {k: round(v, 6) for k, v in dist.items()}


def _check_prob_sum(dist: dict, name: str) -> None:
    total = sum(dist.values())
    if abs(total - 1.0) > 1e-3:
        raise ValueError(
            f"快照拒绝写入：{name}概率和={total:.6f}，偏离1超过容差"
        )


def build_six_play_vector(predict_result: dict) -> dict:
    """从predict()结果构建完整六玩法概率向量。

    要求 predict_result 含 score_matrix_full（model='beidan'时predictor输出）。
    所有概率从同一矩阵聚合；score_top5仅作展示。
    """
    matrix = predict_result.get("score_matrix_full")
    if not matrix:
        raise ValueError(
            "缺少 score_matrix_full：predict() 需用 model='beidan' 调用"
        )
    deriv = predict_result.get("derivatives", {})

    # 1. 胜平负
    wdl = {
        "胜": predict_result["p_home"],
        "平": predict_result["p_draw"],
        "负": predict_result["p_away"],
    }

    # 2. 让球胜平负
    h1x2 = deriv.get("handicap_1x2") or {}
    handicap_wdl = {
        "让胜": h1x2.get("p_home", 0.0),
        "让平": h1x2.get("p_draw", 0.0),
        "让负": h1x2.get("p_away", 0.0),
        "handicap_line": h1x2.get("line"),
    }

    # 3. 比分25类（完整分布）
    score_25 = aggregate_25class(matrix)
    # score_top5 仅展示
    score_top5_display = deriv.get("top_scores", [])[:5]

    # 4. 总进球8类
    total_goals = aggregate_total_goals_8(matrix)

    # 5. 半全场9类
    half_full_raw = deriv.get("half_full_1x2") or {}
    half_full = {k: half_full_raw.get(k, 0.0) for k in HALF_FULL_9}

    # 6. 上下单双4类
    ou = aggregate_ou_4(matrix)

    # 完整性校验（概率和为1）
    _check_prob_sum(wdl, "wdl")
    if h1x2:
        _check_prob_sum(
            {k: handicap_wdl[k] for k in ("让胜", "让平", "让负")},
            "handicap_wdl",
        )
    _check_prob_sum(score_25, "score_25")
    _check_prob_sum(total_goals, "total_goals")
    _check_prob_sum(half_full, "half_full")
    _check_prob_sum(ou, "ou")

    return {
        "wdl": wdl,
        "handicap_wdl": handicap_wdl,
        "score_25": score_25,
        "score_top5_display": score_top5_display,  # 仅展示，不作概率依据
        "total_goals": total_goals,
        "half_full": half_full,
        "ou": ou,
    }


def _has_real_time_evidence(value) -> bool:
    """时间证据校验：必须是非空、合法ISO8601、且不晚于当前时刻。"""
    if not value or not isinstance(value, str):
        return False
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # 禁止未来时间（预设时间戳的典型特征）
    if dt > now:
        return False
    return True


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
    """写入一条北单预测快照（append-only）。

    match_data 需含：lottery_no, seq, league, home, away, kickoff,
        generated_at（真实生成时刻）, available_at（各源可用时间dict，可选）,
        handicap_line（可选）, sp_snapshot（可选，须带采集时间）,
        data_completeness（可选）, skipped/skip_reason（可选）。
    predict_result 为 predict(payload, model='beidan') 的返回。

    返回写入的记录（含 snapshot_id / rerun_of / observation_only）。
    时间证据缺失时自动标 observation_only=true。
    """
    lottery_no = str(match_data["lottery_no"])
    seq = str(match_data["seq"])

    # 时间证据校验
    generated_at = match_data.get("generated_at")
    available_at = match_data.get("available_at") or {}
    time_ok = _has_real_time_evidence(generated_at)
    # 各源 available_at 缺失也算证据不足
    sources_ok = bool(available_at) and all(
        _has_real_time_evidence(v) for v in available_at.values()
        if v is not None
    )
    observation_only = not (time_ok and sources_ok)

    six = build_six_play_vector(predict_result)

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"{lottery_no}.jsonl")

    # rerun_of 链：同一 lottery_no:seq 重复写入时指向前一条
    seen = _existing_seqs(path, lottery_no)
    rerun_count = seen.get(seq, 0)
    rerun_of = None
    if rerun_count > 0:
        rerun_of = f"{lottery_no}:{seq}#r{rerun_count}"

    snapshot_id = f"{lottery_no}:{seq}" + (f"#r{rerun_count + 1}" if rerun_count else "")

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
        "available_at": available_at,
        "asof": match_data.get("asof", generated_at),
        "lambda_home": predict_result.get("lambda_home"),
        "lambda_away": predict_result.get("lambda_away"),
        "p_1x2": [predict_result["p_home"], predict_result["p_draw"],
                  predict_result["p_away"]],
        "six_play_vector": six,
        "score_matrix_full": predict_result.get("score_matrix_full"),
        "model_version": predict_result.get("model_version"),
        "handicap_line": match_data.get("handicap_line"),
        "sp_snapshot": match_data.get("sp_snapshot"),
        "data_completeness": match_data.get("data_completeness"),
        "skipped": bool(match_data.get("skipped", False)),
        "skip_reason": match_data.get("skip_reason"),
        "observation_only": observation_only,
        "observation_reason": (
            None if not observation_only
            else "时间证据缺失：generated_at或各源available_at无真实时间证据"
        ),
        "provenance_unverified": observation_only,
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return record
