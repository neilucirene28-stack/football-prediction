"""Import the 1665-result export as a forward-only, score-only BD-1 prior.

This is a deliberately narrow adapter. Historical SP and the un-timestamped
handicap must never become pre-match features through this route.
"""
from __future__ import annotations

import json
from pathlib import Path

from .snapshot import _datetime


_PERIODS = frozenset(("26092", "26093", "26094", "26095", "26096", "26097", "26098", "26101"))


def _score(raw: object, field: str) -> tuple[int, int]:
    if not isinstance(raw, str):
        raise ValueError(f"{field} 不是比分字符串")
    parts = raw.split("-")
    if len(parts) != 2 or any(not x.isascii() or not x.isdigit() for x in parts):
        raise ValueError(f"{field} 格式非法")
    return int(parts[0]), int(parts[1])


def convert_export_row(row: dict) -> dict:
    """Validate provenance; retain only scores and a conservative first-seen time."""
    if row.get("lottery_no") not in _PERIODS or not str(row.get("seq", "")).isdigit():
        raise ValueError("北单期号/场号非法")
    if (row.get("source") != "beidan_backtest_periods"
            or row.get("usage") != "score_distribution_only"
            or row.get("provenance_unverified") is not True
            or row.get("result_available_at") is not None
            or row.get("handicap_collected_at") is not None):
        raise ValueError("原始赛果可用时间与来源边界不符")
    kickoff = _datetime(row.get("kickoff"), "kickoff")
    verified = _datetime(row.get("verified_at"), "verified_at")
    if verified <= kickoff:
        raise ValueError("首次核验不得早于开球")
    ft_h, ft_a = _score(row.get("full_score"), "full_score")
    ht_h, ht_a = _score(row.get("half_score"), "half_score")
    if ht_h > ft_h or ht_a > ft_a:
        raise ValueError("半场进球超过全场")
    for key in ("home", "away", "league"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"原始{key}缺失，无法进行身份对账")
    return {
        "match_id": f"{row['lottery_no']}:{row['seq']}",
        # Names are audit join keys, never an inferred team identity or L1 feature.
        "observed_home": row["home"], "observed_away": row["away"],
        "observed_competition": row["league"],
        "competition_family": None,  # 尚无经过核验的赛事族映射
        "kickoff_at": kickoff.isoformat(),
        "result_available_at": None,
        "verified_at": verified.isoformat(),
        "regular_time": True,
        "ft_home": ft_h, "ft_away": ft_a,
        "ht_home": ht_h, "ht_away": ht_a,
    }


def load_verified_history(path: str | Path) -> list[dict]:
    """Load a local copy of the verified export, rejecting duplicates and drift."""
    rows = []
    with Path(path).open(encoding="utf-8") as src:
        for line_no, line in enumerate(src, 1):
            if not line.strip():
                continue
            try:
                rows.append(convert_export_row(json.loads(line)))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"历史赛果第 {line_no} 行无效: {exc}") from exc
    ids = [r["match_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("历史赛果存在重复期号/场号")
    return rows
