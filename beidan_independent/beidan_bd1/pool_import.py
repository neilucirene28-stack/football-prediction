"""Read the two 26103 exports as an offered-pool roster, never as training data.

The exports lack per-feature first-seen times and actual generation times.  The
handicap and SP values are deliberately discarded; Beijing local kickoff is
attached to +08:00 only because the source field definition states its zone.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re


_LOCAL_KICKOFF = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
_BEIJING = timezone(timedelta(hours=8))


def _rows(path: str | Path):
    with Path(path).open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第{line_no}行JSON无效") from exc
            if not isinstance(row, dict):
                raise ValueError(f"第{line_no}行必须为对象")
            yield row


def load_offered_pool(shadow_path: str | Path, skipped_path: str | Path, *,
                      period: str = "26103", expected_total: int = 193) -> list[dict]:
    """Return all offered fixtures and failures; never infer past feature times.

    Raises on a missing, duplicate, inconsistent or partial pool.  The exported
    list itself still needs independent verification against the official
    on-sale roster before any coverage claim can be called authoritative.
    """
    if not period.isdigit() or expected_total < 1:
        raise ValueError("期号或预期场数无效")
    found = {}
    for status, path in (("shadow", shadow_path), ("skipped", skipped_path)):
        for row in _rows(path):
            seq = str(row.get("seq", ""))
            if (row.get("lottery_no") != period or not seq.isdigit()
                    or row.get("match_id") != f"{period}-{seq}"):
                raise ValueError("期号/场号/赛事ID不一致")
            key = (period, seq)
            if key in found:
                raise ValueError("赛程场号重复")
            if row.get("missing_flag") is not (status == "skipped"):
                raise ValueError("预测/跳过状态不一致")
            if status == "shadow" and (row.get("observation_only") is not True
                                       or row.get("provenance_unverified") is not True
                                       or row.get("generated_at") is not None
                                       or row.get("available_at") is not None):
                raise ValueError("旧影子记录不能冒充真实赛前快照")
            raw_kickoff = row.get("kickoff")
            if not isinstance(raw_kickoff, str) or not _LOCAL_KICKOFF.fullmatch(raw_kickoff):
                raise ValueError("开球时间格式或时区定义不符")
            kickoff = datetime.strptime(raw_kickoff, "%Y-%m-%d %H:%M").replace(tzinfo=_BEIJING)
            for field in ("home", "away", "league"):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError(f"{field} 缺失")
            # The 26103 export is a mixed-sport Beidan board. Its six named
            # tennis/ice-hockey entries must never enter a football goal model.
            sport = {"中网女单": "tennis", "美职冰": "ice_hockey"}.get(row["league"], "football")
            found[key] = {
                "period": period, "seq": seq, "match_id": row["match_id"],
                "league": row["league"], "home": row["home"], "away": row["away"],
                "sport": sport, "sport_basis": "26103_legacy_league_label_not_officially_verified",
                "kickoff_at": kickoff.isoformat(),
                "kickoff_zone_basis": "export_field_defs_beijing_not_independently_verified",
                "status": status, "skip_reason": row.get("skip_reason") if status == "skipped" else None,
                "competition_family": None, "official_handicap": None,
                "source_available_at": None, "generated_at": None,
                "observation_only": True, "as_of_backtest_eligible": False,
            }
    if len(found) != expected_total:
        raise ValueError(f"赛程仅{len(found)}场，预期{expected_total}场")
    return [found[key] for key in sorted(found, key=lambda k: int(k[1]))]
