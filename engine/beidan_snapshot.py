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
    """从全比分矩阵聚合北单官方31类分布。"""
    dist = {c: 0.0 for grp in BEIDAN_SCORE_31.values() for c in grp}
    n = len(matrix)
    for h in range(n):
        row = matrix[h]
        for a in range(len(row)):
            p = row[a]
            if p:
                dist[score_to_beidan_class(h, a)] += p
    return {k: round(v, 6) for k, v in dist.items()}


# 兼容别名（旧名保留，指向31类）
def aggregate_25class(matrix):
    """已废弃：25类是下半场比分。请用 aggregate_31class。"""
    raise NotImplementedError("25类已废弃（系下半场比分），请用 aggregate_31class")


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

    # 2. 让球胜平负：缺失时统一返回None，不返回零概率/None混合dict
    h1x2 = deriv.get("handicap_1x2")
    if h1x2 and h1x2.get("line") is not None:
        handicap_wdl = {
            "让胜": h1x2["p_home"],
            "让平": h1x2["p_draw"],
            "让负": h1x2["p_away"],
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

    # 5. 半全场9类
    half_full_raw = deriv.get("half_full_1x2") or {}
    half_full = {k: half_full_raw.get(k, 0.0) for k in HALF_FULL_9}

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

    # 时间证据校验：顺序 available_at <= asof <= generated_at < kickoff
    time_violation = _check_time_ordering(match_data)
    observation_only = time_violation is not None

    six = build_six_play_vector(predict_result)

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"{lottery_no}.jsonl")

    # rerun_of 链：同一 lottery_no:seq 重复写入时指向实际存在的前一条
    # 第一条 snapshot_id="26103:1"（无后缀），第二条 rerun_of 必须指向它
    existing_ids: list[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("lottery_no") == lottery_no and str(rec.get("seq")) == seq:
                    existing_ids.append(rec.get("snapshot_id", ""))

    rerun_of = existing_ids[-1] if existing_ids else None
    if existing_ids:
        snapshot_id = f"{lottery_no}:{seq}#r{len(existing_ids) + 1}"
    else:
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
        "observation_reason": time_violation,
        "provenance_unverified": observation_only,
    }

    # 原子写入：临时文件 + fsync + rename
    import tempfile
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".",
        prefix=".snapshot_tmp_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # 追加模式语义：先读旧内容再整体写回（保证单次rename原子性）
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as old:
                    f.write(old.read())
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return record
