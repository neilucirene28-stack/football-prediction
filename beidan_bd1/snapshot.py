"""北单 BD-1 影子预测的不可覆盖快照。

此模块不改变 v2.8 预测概率。它为未来的 as-of 滚动验证保存证据；
没有逐场来源时间的旧数据只能存为 observation_only。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "bd1-snapshot-4"  # v4: retain the training result lineage
_MATCH_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TRAIN_ID = re.compile(r"^[A-Za-z0-9_:-]{1,80}$")
_THREE = ("胜", "平", "负")
_HALF_FULL = tuple(a + b for a in _THREE for b in _THREE)
_ODD_EVEN = ("上单", "上双", "下单", "下双")
_GOALS = tuple(str(k) for k in range(7)) + ("7+",)
_SCORE_HOME = ("1-0", "2-0", "2-1", "3-0", "3-1", "3-2",
               "4-0", "4-1", "4-2", "5-0", "5-1", "5-2")
_SCORE_DRAW = ("0-0", "1-1", "2-2", "3-3")
_SCORE_AWAY = tuple("-".join(reversed(s.split("-"))) for s in _SCORE_HOME)


def _datetime(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是带时区的 ISO 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} 时间格式错误") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} 必须带时区")
    return parsed.astimezone(timezone.utc)


def _probabilities(vector: dict, field: str, expected: tuple[str, ...] | None = None) -> dict:
    if not isinstance(vector, dict) or not vector:
        raise ValueError(f"{field} 缺少概率向量")
    if expected is not None and set(vector) != set(expected):
        raise ValueError(f"{field} 类别不完整")
    values = {}
    for key, value in vector.items():
        if not isinstance(key, str) or not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"{field} 概率类型错误")
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{field} 概率超出范围")
        values[key] = float(value)
    if abs(sum(values.values()) - 1) > 1e-8:
        raise ValueError(f"{field} 概率未归一")
    return values


def _positive_lambda(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} 非法")
    return float(value)


def build_snapshot(*, match_id: str, period: str, kickoff_at: str, asof_at: str,
                   model_version: str, vectors: dict[str, dict],
                   sources: list[dict], lambda_home: float | None,
                   lambda_away: float | None, handicap: int | None,
                   generated_at: str | None = None, status: str = "shadow",
                   input_sources: dict[str, str] | None = None,
                   competition_family: str | None = None,
                   synthetic_sample: bool = False,
                   training_lineage: dict | None = None) -> dict:
    """建立逐场证据记录。asof_at 是预测请求的时点，不冒充来源采集时间。

    ``sources`` 每项仅允许 name、source_match_id、available_at、status。
    ``input_sources`` 明确绑定实际输入字段到上述来源名称。L3 至少需要
    fixture/history；若使用已核验赛事族或让球线，需相应 family/handicap。
    未核验赛事族只能传 null 并回退根先验。旧记录没有绑定时
    只能 observation_only，不能凭一条无关的时间戳宣称可回测。
    """
    if not isinstance(match_id, str) or not _MATCH_ID.fullmatch(match_id):
        raise ValueError("match_id 非法")
    if not isinstance(period, str) or not _MATCH_ID.fullmatch(period):
        raise ValueError("period 非法")
    if not isinstance(model_version, str) or not model_version:
        raise ValueError("model_version 缺失")
    if status != "shadow":
        raise ValueError("本记录器只接受影子预测，生产发布另设验收")
    if not isinstance(synthetic_sample, bool):
        raise ValueError("synthetic_sample 必须为布尔值")
    kickoff = _datetime(kickoff_at, "kickoff_at")
    asof = _datetime(asof_at, "asof_at")
    generated = _datetime(generated_at, "generated_at") if generated_at else datetime.now(timezone.utc)
    if not asof <= generated < kickoff:
        raise ValueError("要求 asof_at ≤ generated_at < kickoff_at")
    if handicap is not None and (isinstance(handicap, bool) or not isinstance(handicap, int)):
        raise ValueError("北单让球线必须为整数或 null")
    if competition_family is not None and (
        not isinstance(competition_family, str) or not _MATCH_ID.fullmatch(competition_family)
    ):
        raise ValueError("赛事族必须是规范名称或 null")
    if not isinstance(sources, list) or not sources:
        raise ValueError("必须列出实际使用的数据源")
    clean_sources = []
    verified = not synthetic_sample
    source_names = set()
    for src in sources:
        if not isinstance(src, dict) or set(src) - {"name", "source_match_id", "available_at", "status"}:
            raise ValueError("来源字段超出白名单")
        name = src.get("name")
        if not isinstance(name, str) or not _MATCH_ID.fullmatch(name):
            raise ValueError("来源名称缺失")
        if name in source_names:
            raise ValueError("来源名称重复，无法绑定输入字段")
        source_names.add(name)
        source_match_id = src.get("source_match_id")
        if source_match_id is not None and (
            not isinstance(source_match_id, str) or not _MATCH_ID.fullmatch(source_match_id)
        ):
            raise ValueError("source_match_id 只允许短标识，不允许 URL/凭证")
        if src.get("status", "ok") not in ("ok", "missing", "failed"):
            raise ValueError("来源状态非法")
        available = src.get("available_at")
        if available is None or src.get("status", "ok") != "ok":
            verified = False
        elif _datetime(available, "available_at") > asof:
            raise ValueError(f"来源 {name} 在预测时尚不可得")
        clean_sources.append({
            "name": name,
            "source_match_id": source_match_id,
            "available_at": available,
            "status": src.get("status", "ok"),
        })
    required = {"fixture", "history"}
    if competition_family is not None:
        required.add("family")
    if handicap is not None:
        required.add("handicap")
    if input_sources is None:
        verified = False
        clean_input_sources = None
    else:
        if not isinstance(input_sources, dict) or not required <= set(input_sources):
            raise ValueError("实际输入字段缺少来源绑定")
        if any(not isinstance(field, str) or not _MATCH_ID.fullmatch(field)
               or not isinstance(name, str) or name not in source_names
               for field, name in input_sources.items()):
            raise ValueError("输入字段绑定的来源不存在或名称非法")
        by_name = {source["name"]: source for source in clean_sources}
        if any(by_name[name]["status"] != "ok"
               or by_name[name]["available_at"] is None
               for name in input_sources.values()):
            verified = False
        clean_input_sources = dict(input_sources)
    if training_lineage is None:
        verified = False
        clean_lineage = None
    else:
        if not isinstance(training_lineage, dict) or set(training_lineage) != {
            "match_ids", "latest_available_at"
        }:
            raise ValueError("训练历史血缘字段不完整")
        used_ids = training_lineage["match_ids"]
        if (not isinstance(used_ids, list) or not used_ids
                or any(not isinstance(i, str) or not _TRAIN_ID.fullmatch(i) for i in used_ids)
                or len(set(used_ids)) != len(used_ids)):
            raise ValueError("训练历史ID无效或重复")
        latest = _datetime(training_lineage["latest_available_at"], "latest_available_at")
        if latest > asof:
            raise ValueError("训练赛果在预测时尚不可得")
        if clean_input_sources is not None:
            history_src = next((s for s in clean_sources
                                if s["name"] == clean_input_sources["history"]), None)
            if (history_src is not None and history_src["available_at"] is not None
                    and _datetime(history_src["available_at"], "available_at") != latest):
                raise ValueError("训练历史来源时间与血缘不一致")
        clean_lineage = {"match_ids": list(used_ids),
                         "latest_available_at": latest.isoformat()}

    if not isinstance(vectors, dict) or set(vectors) != {
        "wdl", "handicap_wdl", "score", "total_goals", "half_full", "odd_even"
    }:
        raise ValueError("六玩法概率向量必须齐全")
    if handicap is None and vectors["handicap_wdl"] is not None:
        raise ValueError("无让球线时不能伪造让球概率")
    if handicap is not None and vectors["handicap_wdl"] is None:
        raise ValueError("有让球线时须提供完整让球概率")
    clean_vectors = {
        "wdl": _probabilities(vectors["wdl"], "wdl", _THREE),
        "handicap_wdl": (_probabilities(vectors["handicap_wdl"], "handicap_wdl", _THREE)
                         if handicap is not None else None),
        "score": _probabilities(vectors["score"], "score"),
        "total_goals": _probabilities(vectors["total_goals"], "total_goals", _GOALS),
        "half_full": _probabilities(vectors["half_full"], "half_full", _HALF_FULL),
        "odd_even": _probabilities(vectors["odd_even"], "odd_even", _ODD_EVEN),
    }
    for score in clean_vectors["score"]:
        if not re.fullmatch(r"(?:0|[1-9]\d*)-(?:0|[1-9]\d*)", score):
            raise ValueError("score 类别必须是主队进球-客队进球")
    from .math_core import handicap_1x2, match_probs
    scores = [(tuple(map(int, label.split("-"))), p)
              for label, p in clean_vectors["score"].items()]
    n = max(max(h, a) for (h, a), _ in scores) + 1
    matrix = [[0.0] * n for _ in range(n)]
    for (h, a), p in scores:
        matrix[h][a] = p
    if any(abs(actual - clean_vectors["wdl"][key]) > 1e-8
           for actual, key in zip(match_probs(matrix), _THREE)):
        raise ValueError("胜平负概率与比分矩阵不一致")
    if handicap is not None and any(
        abs(actual - clean_vectors["handicap_wdl"][key]) > 1e-8
        for actual, key in zip(handicap_1x2(matrix, handicap), _THREE)
    ):
        raise ValueError("让球概率与比分矩阵不一致")
    totals = {k: 0.0 for k in _GOALS}
    for (h, a), p in scores:
        label = str(h + a) if h + a < 7 else "7+"
        totals[label] = totals.get(label, 0.0) + p
    if set(totals) != set(clean_vectors["total_goals"]) or any(
        abs(p - clean_vectors["total_goals"][k]) > 1e-8 for k, p in totals.items()
    ):
        raise ValueError("总进球概率与比分矩阵不一致")
    for outcome in _THREE:
        if abs(sum(p for k, p in clean_vectors["half_full"].items() if k[-1] == outcome)
               - clean_vectors["wdl"][outcome]) > 1e-8:
            raise ValueError("半全场全场边际与胜平负不一致")
    odd_even = {k: 0.0 for k in _ODD_EVEN}
    for (h, a), p in scores:
        t = h + a
        odd_even[("上" if t >= 3 else "下") + ("单" if t % 2 else "双")] += p
    if any(abs(p - clean_vectors["odd_even"][k]) > 1e-8 for k, p in odd_even.items()):
        raise ValueError("上下单双概率与比分矩阵不一致")
    score_31 = {k: clean_vectors["score"].get(k, 0.0)
                for k in (*_SCORE_HOME, *_SCORE_DRAW, *_SCORE_AWAY)}
    score_31.update({"胜其他": 0.0, "平其他": 0.0, "负其他": 0.0})
    for (h, a), p in scores:
        label = f"{h}-{a}"
        if label not in score_31:
            score_31["胜其他" if h > a else ("平其他" if h == a else "负其他")] += p
    return {
        "schema_version": SCHEMA_VERSION, "status": status,
        "period": period, "match_id": match_id,
        "kickoff_at": kickoff_at, "asof_at": asof_at,
        "generated_at": generated.isoformat(), "model_version": model_version,
        "lambda_home": _positive_lambda(lambda_home, "lambda_home"),
        "lambda_away": _positive_lambda(lambda_away, "lambda_away"),
        "handicap": handicap, "competition_family": competition_family,
        "sources": clean_sources, "vectors": clean_vectors,
        "input_sources": clean_input_sources,
        "training_lineage": clean_lineage,
        "score_31": score_31,
        "synthetic_sample": synthetic_sample,
        "provenance_unverified": not verified,
        "as_of_backtest_eligible": verified,
        "observation_only": not verified,
    }


def save_snapshot(root: str | Path, record: dict) -> Path:
    """用 O_EXCL 建立不可覆盖逐版文件；同一场可在新时点另存快照。"""
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema_version 错误")
    if not isinstance(record.get("period"), str) or not _MATCH_ID.fullmatch(record["period"]):
        raise ValueError("period 非法")
    if not isinstance(record.get("match_id"), str) or not _MATCH_ID.fullmatch(record["match_id"]):
        raise ValueError("match_id 非法")
    folder = Path(root) / record["period"]
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _datetime(record.get("generated_at"), "generated_at").strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f'{record["match_id"]}__{stamp}.json'
    data = (json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path
