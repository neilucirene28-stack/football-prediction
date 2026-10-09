"""Conservative canonical-ID evidence join for future BD-1 L1 observations.

An archived provider payload and explicit human audit event are required. The
hash guards payload drift; it does not certify the provider or the auditor.
Unmatched fixtures stay on L3 rather than guessing identities from names.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .snapshot import _datetime, _MATCH_ID, _TRAIN_ID


_PAYLOAD_KEYS = {"home", "away", "league", "kickoff_at",
                 "home_id", "away_id", "competition_id"}


def payload_digest(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _evidence(row: dict) -> dict:
    if not isinstance(row, dict) or set(row) != {
        "match_id", "source_name", "source_match_id", "collected_at",
        "audited_at", "auditor_id", "audit_status", "payload", "payload_sha256"
    }:
        raise ValueError("身份记录字段不完整或含未知字段")
    for key in ("match_id", "source_name", "source_match_id", "auditor_id"):
        pattern = _TRAIN_ID if key == "match_id" else _MATCH_ID
        if not isinstance(row[key], str) or not pattern.fullmatch(row[key]):
            raise ValueError(f"{key} 必须为短规范标识")
    if row["audit_status"] != "approved":
        raise ValueError("身份映射尚未审核通过")
    payload = row["payload"]
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("身份来源归档字段不完整")
    for key in ("home", "away", "league"):
        if not isinstance(payload[key], str) or not payload[key].strip():
            raise ValueError("原始名称不可为空")
    for key in ("home_id", "away_id", "competition_id"):
        if not isinstance(payload[key], str) or not _MATCH_ID.fullmatch(payload[key]):
            raise ValueError("来源规范ID非法")
    if payload["home_id"] == payload["away_id"]:
        raise ValueError("规范主客ID相同")
    _datetime(payload["kickoff_at"], "payload.kickoff_at")
    collected = _datetime(row["collected_at"], "collected_at")
    audited = _datetime(row["audited_at"], "audited_at")
    if not collected <= audited:
        raise ValueError("来源采集与身份审核时间倒置")
    if not isinstance(row["payload_sha256"], str) or row["payload_sha256"] != payload_digest(payload):
        raise ValueError("身份归档摘要与payload不一致")
    return row


def load_identity_evidence(path: str | Path) -> list[dict]:
    out = []
    with Path(path).open(encoding="utf-8") as src:
        for n, line in enumerate(src, 1):
            if line.strip():
                try:
                    out.append(_evidence(json.loads(line)))
                except (ValueError, TypeError, KeyError) as exc:
                    raise ValueError(f"身份记录第{n}行无效: {exc}") from exc
    ids = [r["match_id"] for r in out]
    if len(ids) != len(set(ids)):
        raise ValueError("同一比赛有重复身份核验记录")
    return out


def attach_identities(targets: list[dict], evidence: list[dict], *,
                      kind: str) -> tuple[list[dict], dict]:
    """Join evidence on stable fixture ID, exact names and UTC kickoff.

    `kind` is `history` for imported results or `fixtures` for an offered
    pool. A partial evidence file is valid; unknown or contradictory evidence
    is a hard error rather than a guessed correction.
    """
    if kind not in ("history", "fixtures"):
        raise ValueError("身份对账对象非法")
    by_id = {r["match_id"]: r for r in targets}
    if len(by_id) != len(targets):
        raise ValueError("目标比赛ID重复")
    evidence_by_id = {}
    for raw in evidence:
        r = _evidence(raw)
        match_id = r["match_id"]
        if match_id in evidence_by_id or match_id not in by_id:
            raise ValueError("身份记录重复或未匹配到目标比赛")
        evidence_by_id[match_id] = r
    updated = []
    for target in targets:
        r = evidence_by_id.get(target["match_id"])
        if r is None:
            updated.append(dict(target))
            continue
        p = r["payload"]
        keys = (("observed_home", "observed_away", "observed_competition")
                if kind == "history" else ("home", "away", "league"))
        if (tuple(target.get(k) for k in keys) != (p["home"], p["away"], p["league"])
                or _datetime(target["kickoff_at"], "kickoff_at")
                != _datetime(p["kickoff_at"], "payload.kickoff_at")):
            raise ValueError(f"{target['match_id']} 来源身份与原赛程不一致")
        if kind == "fixtures" and _datetime(r["audited_at"], "audited_at") >= _datetime(target["kickoff_at"], "kickoff_at"):
            raise ValueError("赛程身份审核不在开球前")
        out = dict(target)
        out.update({key: p[key] for key in ("home_id", "away_id", "competition_id")})
        out.update(identity_verified=True, identity_source=r["source_name"],
                   identity_verified_at=r["audited_at"])
        if kind == "fixtures":
            sources = list(out.get("sources") or [])
            if any(s.get("name") == r["source_name"] for s in sources):
                raise ValueError("规范ID证据与现有来源名称冲突")
            sources.append({"name": r["source_name"],
                            "source_match_id": r["source_match_id"],
                            "available_at": r["audited_at"], "status": "ok"})
            bindings = dict(out.get("input_sources") or {})
            bindings.setdefault("fixture", r["source_name"])
            bindings.update({key: r["source_name"]
                             for key in ("home_id", "away_id", "competition_id")})
            out.update(sources=sources, input_sources=bindings)
        updated.append(out)
    return updated, {"target_n": len(targets), "audited_identity_n": len(evidence_by_id),
                     "identity_coverage": len(evidence_by_id) / len(targets) if targets else 0.0,
                     "unmatched_n": len(targets) - len(evidence_by_id)}


def main() -> None:
    ap = argparse.ArgumentParser(description="按归档来源证据对账北单规范身份")
    ap.add_argument("--kind", required=True, choices=("history", "fixtures"))
    ap.add_argument("--input", required=True, help="规范化历史或赛程JSONL")
    ap.add_argument("--evidence", required=True, help="已审核身份来源证据JSONL")
    ap.add_argument("--out", required=True, help="新建输出JSONL，不覆盖现有文件")
    args = ap.parse_args()
    targets = []
    with Path(args.input).open(encoding="utf-8") as src:
        for n, line in enumerate(src, 1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"输入第{n}行不是对象")
                targets.append(row)
    updated, report = attach_identities(targets, load_identity_evidence(args.evidence),
                                        kind=args.kind)
    dest = Path(args.out)
    with dest.open("x", encoding="utf-8") as out:
        for row in updated:
            out.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
