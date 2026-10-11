"""Chronological replay audit of already frozen BD-1 shadow forecasts.

Checks training lineage against supplied outcome/archive clocks. This cannot
prove the archive was immutable at its claimed time or that hyperparameters
were chosen without looking at future results; release approval is external.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from .evaluation import audit_frozen_pair
from .snapshot import _datetime


def _lineage(snapshot: dict, history: dict[str, dict]) -> None:
    asof = _datetime(snapshot.get("asof_at"), "asof_at")
    lineage = snapshot.get("training_lineage")
    if not isinstance(lineage, dict) or not lineage.get("match_ids"):
        raise ValueError("训练血缘缺失")
    stamps = []
    for match_id in lineage["match_ids"]:
        if match_id not in history:
            raise ValueError(f"训练ID {match_id} 不在赛果档案")
        row = history[match_id]
        kickoff = _datetime(row.get("kickoff_at"), "kickoff_at")
        observed = [_datetime(row[key], key)
                    for key in ("result_available_at", "verified_at")
                    if row.get(key) is not None]
        if not observed or kickoff >= asof or max(observed) > asof:
            raise ValueError("训练赛果在预测时尚不可得")
        if snapshot.get("identity_ids") is not None:
            if (row.get("identity_verified") is not True
                    or not row.get("identity_source")
                    or row.get("identity_verified_at") is None):
                raise ValueError("L1训练赛果身份来源未经核验")
            observed.append(_datetime(row["identity_verified_at"], "identity_verified_at"))
            if observed[-1] > asof:
                raise ValueError("L1训练身份在预测时尚不可得")
        stamps.extend(observed)
    if _datetime(lineage.get("latest_available_at"), "latest_available_at") != max(stamps):
        raise ValueError("快照训练最晚时间与原始赛果档案不一致")


def audit_chronological_replay(*, folds: list[dict], history: list[dict],
                               evaluated_at: str, frozen_models: dict) -> dict:
    """Compare complete, disjoint consecutive offered pools, weighting by match.

    Each fold has period, cutoff_at, fixtures, baseline, candidate. Both
    snapshots must be available before the same decision cutoff. This is an
    audit scaffold, not automatic model selection or production approval.
    """
    if not isinstance(folds, list) or len(folds) < 2:
        raise ValueError("至少需要两个连续的历史外测试期")
    if (not isinstance(frozen_models, dict)
            or set(frozen_models) != {"baseline", "candidate"}):
        raise ValueError("基线和候选参数冻结元数据缺失")
    h = {row["match_id"]: row for row in history}
    if len(h) != len(history):
        raise ValueError("赛果档案赛事ID重复")
    previous_cutoff = None
    seen = set()
    reports = []
    for fold in folds:
        if not isinstance(fold, dict) or set(fold) != {
            "period", "cutoff_at", "fixtures", "baseline", "candidate"
        }:
            raise ValueError("滚动期字段不完整")
        cutoff = _datetime(fold["cutoff_at"], "cutoff_at")
        if previous_cutoff is not None and cutoff <= previous_cutoff:
            raise ValueError("滚动期决策时点非严格递增")
        previous_cutoff = cutoff
        for result in fold["fixtures"]:
            if result.get("period") != fold["period"]:
                raise ValueError("滚动期赛程期号不一致")
            key = (result.get("period"), result.get("match_id"))
            if key in seen:
                raise ValueError("历史外测试比赛跨期重复")
            seen.add(key)
            if _datetime(result.get("decision_cutoff_at"), "decision_cutoff_at") != cutoff:
                raise ValueError("测试比赛决策时点与滚动期不一致")
            if cutoff >= _datetime(result.get("kickoff_at"), "kickoff_at"):
                raise ValueError("滚动期含决策时已开球的比赛")
        for label in ("baseline", "candidate"):
            frozen = frozen_models[label]
            if (not isinstance(frozen, dict) or set(frozen) != {"model_version", "frozen_at", "artifact_sha256", "artifact_path"}
                    or not isinstance(frozen["model_version"], str)
                    or not isinstance(frozen["artifact_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", frozen["artifact_sha256"])
                    or not isinstance(frozen["artifact_path"], str)
                    or _datetime(frozen["frozen_at"], "frozen_at") > cutoff):
                raise ValueError("模型配置未在测试期决策前冻结")
            if hashlib.sha256(Path(frozen["artifact_path"]).read_bytes()).hexdigest() != frozen["artifact_sha256"]:
                raise ValueError("冻结配置的制品摘要与文件不一致")
            for snapshot in fold[label]:
                if snapshot.get("period") != fold["period"] or snapshot.get("model_version") != frozen["model_version"]:
                    raise ValueError("冻结模型版本或预测期号不一致")
                if _datetime(frozen["frozen_at"], "frozen_at") > _datetime(snapshot.get("asof_at"), "asof_at"):
                    raise ValueError("模型配置在预测时尚未冻结")
                _lineage(snapshot, h)
        audit = audit_frozen_pair(
            baseline=fold["baseline"], candidate=fold["candidate"],
            fixtures=fold["fixtures"], evaluated_at=evaluated_at)
        reports.append({"period": fold["period"], **audit})
    n = sum(r["paired_n"] for r in reports)
    b = sum(r["brier_baseline"] * r["paired_n"] for r in reports) / n
    c = sum(r["brier_candidate"] * r["paired_n"] for r in reports) / n
    return {"label": "chronological_replay_audit_not_release_approval",
            "folds": reports, "offered_n": sum(r["offered_n"] for r in reports),
            "paired_n": n, "coverage": n / sum(r["offered_n"] for r in reports),
            "brier_baseline": b, "brier_candidate": c, "delta_brier": c - b,
            "observed_non_degradation": c <= b + 1e-12,
            "production_gate_passed": False,
            "unverified_release_factors": ["official_pool_completeness", "archive_immutability",
                                           "parameter_selection_independence", "sample_power"]}
