"""来源门控缺口测试（GPT复核24d63ac：最后一个来源门控缺口）。

1. 让球线无来源门控：hc有值但available_at只有战绩源 → observation_only=true
2. SP collected_at与available_at不对应 → observation_only=true
3. collected_at > asof → observation_only=true
每项测试在当前代码上必须失败，修复后必须通过。
"""
import json
import os
import tempfile

import pytest

from engine.beidan_snapshot import (
    write_snapshot,
    _has_real_time_evidence,
)


def _mk_match(**kw):
    base = {
        "lottery_no": "TEST",
        "seq": "1",
        "league": "测试联赛",
        "home": "主队A",
        "away": "客队B",
        "kickoff": "2026-09-01T20:00:00+08:00",
        "generated_at": "2026-09-01T10:00:00+08:00",
        "available_at": {"espn": "2026-09-01T09:00:00+08:00"},
        "asof": "2026-09-01T09:30:00+08:00",
    }
    base.update(kw)
    return base


def _mk_predict():
    return {
        "p_home": 0.5,
        "p_draw": 0.25,
        "p_away": 0.25,
        "p_final_full": [0.5, 0.25, 0.25],
        "lambda_home": 1.8,
        "lambda_away": 1.2,
        "lambda_home_full": 1.8,
        "lambda_away_full": 1.2,
        "model_version": "2.10",
        "score_matrix_full": [[0.357, 0.214], [0.286, 0.143]],
        "derivatives": {
            "half_full_1x2": {
                "胜胜": 0.30, "胜平": 0.10, "胜负": 0.10,
                "平胜": 0.10, "平平": 0.10, "平负": 0.05,
                "负胜": 0.10, "负平": 0.05, "负负": 0.10,
            },
            "handicap_1x2": {"p_home": 0.4, "p_draw": 0.25, "p_away": 0.35, "line": -1},
            "total_goals_exact": {"0": 0.05, "1": 0.15, "2": 0.25, "3": 0.25,
                                  "4": 0.15, "5": 0.10, "6": 0.03, "7+": 0.02},
            "top_scores": [{"score": "1-0", "prob": 0.12}],
        },
    }


# ---- 缺口1：让球线无来源门控 ----
def test_handicap_without_source_evidence_forces_observation_only():
    """handicap_line有值但available_at只有战绩源（无让球源证据）→ observation_only=true。

    runner的 hc=m.get("handicap") 直接进 payload.handicap_line 参与预测，
    但 snap_match.available_at 只记战绩源时间。无让球采集证据时不能标 false。
    """
    m = _mk_match(
        handicap_line=-1,  # 有让球线，参与了预测
        # available_at 只有 espn（战绩源），没有 handicap/让球源
    )
    rec = write_snapshot(m, _mk_predict())
    assert rec["observation_only"] is True, (
        "handicap_line=-1参与预测但无让球源available_at证据，"
        "必须标 observation_only=true（不准用战绩采集时间替代）"
    )


def test_handicap_with_source_evidence_can_pass():
    """handicap_line有值 + 让球源available_at真实证据 → 允许 observation_only=false。"""
    m = _mk_match(
        handicap_line=-1,
        available_at={
            "espn": "2026-09-01T09:00:00+08:00",
            "handicap_line": "2026-09-01T08:30:00+08:00",  # 让球源真实采集时间
        },
    )
    rec = write_snapshot(m, _mk_predict())
    assert rec["observation_only"] is False, (
        f"让球源证据齐全时不应标observation_only=true，原因: {rec.get('observation_reason')}"
    )


def test_handicap_none_no_gate():
    """handicap_line=None（缺失）时不触发让球门控（upset_risk默认0是既有模型行为）。"""
    m = _mk_match(handicap_line=None)
    rec = write_snapshot(m, _mk_predict())
    # 不应因让球缺失而标 observation_only（其他检查通过时）
    assert "让球" not in (rec.get("observation_reason") or ""), (
        "handicap_line=None 时不应触发让球来源门控"
    )


# ---- 缺口2：SP collected_at与available_at不对应 ----
def test_sp_collected_at_mismatch_available_at():
    """sp_snapshot.collected_at 与 available_at[sp源] 不一致 → observation_only=true。"""
    m = _mk_match(
        available_at={
            "espn": "2026-09-01T09:00:00+08:00",
            "sp_odds": "2026-09-01T08:00:00+08:00",  # SP源真实采集时间
        },
        sp_snapshot={
            "sp_wdl": {"胜": 2.0, "平": 3.0, "负": 3.5},
            # collected_at 与 sp_odds 的 available_at 差2小时 → 不对应
            "collected_at": "2026-09-01T10:00:00+08:00",
        },
    )
    rec = write_snapshot(m, _mk_predict())
    assert rec["observation_only"] is True, (
        "sp_snapshot.collected_at(10:00)与available_at[sp_odds](08:00)不一致，"
        "必须标 observation_only=true"
    )


def test_sp_collected_at_after_asof():
    """sp_snapshot.collected_at > asof → observation_only=true。"""
    m = _mk_match(
        asof="2026-09-01T09:30:00+08:00",
        available_at={
            "espn": "2026-09-01T09:00:00+08:00",
            "sp_odds": "2026-09-01T09:00:00+08:00",
        },
        sp_snapshot={
            "sp_wdl": {"胜": 2.0, "平": 3.0, "负": 3.5},
            # collected_at 晚于 asof
            "collected_at": "2026-09-01T09:45:00+08:00",
        },
    )
    rec = write_snapshot(m, _mk_predict())
    assert rec["observation_only"] is True, (
        "sp_snapshot.collected_at(09:45)晚于asof(09:30)，必须标 observation_only=true"
    )


def test_sp_collected_at_matches_source_passes():
    """collected_at与sp源available_at一致（容差内）且<=asof → 允许通过。"""
    m = _mk_match(
        available_at={
            "espn": "2026-09-01T09:00:00+08:00",
            "sp_odds": "2026-09-01T09:00:00+08:00",
        },
        sp_snapshot={
            "sp_wdl": {"胜": 2.0, "平": 3.0, "负": 3.5},
            # 与 sp_odds 的 available_at 一致
            "collected_at": "2026-09-01T09:00:30+08:00",
        },
    )
    rec = write_snapshot(m, _mk_predict())
    assert rec["observation_only"] is False, (
        f"SP时间证据一致时不应标observation_only=true，原因: {rec.get('observation_reason')}"
    )


def test_sp_collected_at_no_source_in_available_at():
    """collected_at有值但available_at中无SP源 → observation_only=true。"""
    m = _mk_match(
        # available_at 只有 espn，没有 sp 源
        sp_snapshot={
            "sp_wdl": {"胜": 2.0, "平": 3.0, "负": 3.5},
            "collected_at": "2026-09-01T09:00:00+08:00",
        },
    )
    rec = write_snapshot(m, _mk_predict())
    assert rec["observation_only"] is True, (
        "sp_snapshot.collected_at有值但available_at无SP源对应，"
        "必须标 observation_only=true"
    )
