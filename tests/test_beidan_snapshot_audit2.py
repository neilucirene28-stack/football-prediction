"""GPT复核审计：86c4f7d后续5项缺陷的针对性测试。

每项测试在当前代码上必须失败，修复后必须通过。
"""
import json
import os
import tempfile

import pytest

from engine.beidan_snapshot import (
    _has_real_time_evidence,
    _check_prob_sum,
    build_six_play_vector,
    write_snapshot,
)


def _mk_match(**kw):
    base = {
        "lottery_no": "TEST",
        "seq": "1",
        "league": "测试联赛",
        "home": "主队A",
        "away": "客队B",
        "kickoff": "2026-12-01T20:00:00+08:00",
        "generated_at": "2026-12-01T10:00:00+08:00",
        "available_at": {"espn": "2026-12-01T09:00:00+08:00"},
        "asof": "2026-12-01T09:30:00+08:00",
    }
    base.update(kw)
    return base


def _mk_predict():
    # 构造一个边际一致的预测结果
    # P0数值一致性修复后：predict()输出 p_final_full（未舍入）供快照使用
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
                # 边际必须对上全场：胜胜+平胜+负胜=0.5 等
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


# ---- 缺陷1：SP时间冒充 ----
def test_sp_collected_at_requires_real_evidence():
    """sp_snapshot.collected_at 不能用战绩抓取完成时刻冒充。

    write_snapshot 收到 sp_snapshot 含 collected_at 但无真实SP采集证据时，
    必须标 observation_only=true（或拒绝）。当前实现直接信任传入值。
    """
    m = _mk_match(sp_snapshot={"sp_wdl": {"胜": 2.0, "平": 3.0, "负": 3.5},
                               "collected_at": "2026-12-01T09:55:00+08:00"})
    # 没有 sp_first_seen / sp_available_at 证据，只有孤立的 collected_at
    rec = write_snapshot(m, _mk_predict())
    # 修复后：无SP采集证据 → observation_only 必须为 true
    assert rec["observation_only"] is True, (
        "sp_snapshot.collected_at 无真实采集证据，必须标 observation_only=true"
    )


# ---- 缺陷2：让球线默认0 ----
def test_handicap_missing_must_be_null_not_zero():
    """handicap_line 缺失时必须为 null，不能默认成 0 并参与预测。"""
    from engine.beidan_snapshot import build_six_play_vector
    pred = _mk_predict()
    # 模拟 handicap_1x2 缺失
    del pred["derivatives"]["handicap_1x2"]
    six = build_six_play_vector(pred)
    hw = six.get("handicap_wdl")
    assert hw is None, f"让球缺失时 handicap_wdl 应为 None，实际: {hw}"


# ---- 缺陷3：并发丢记录 ----
def test_snapshot_write_is_concurrent_safe():
    """并发写入不能丢记录：必须用 O_EXCL 独占文件或锁保护的 O_APPEND。

    当前实现读全文件后 os.replace，两个并发写会丢一条。
    本测试检查 write_snapshot 不使用 read-all+replace 模式：
    写入后原文件字节必须保留（通过检查实现方式）。
    """
    import engine.beidan_snapshot as mod
    import inspect
    src = inspect.getsource(mod.write_snapshot)
    # 修复后不应再出现"读全文件再整体写回"的模式
    assert "old.read()" not in src and "f.write(old.read())" not in src, (
        "write_snapshot 仍使用 read-all + replace 模式，并发下会丢记录"
    )


# ---- 缺陷4：合成样本标记 ----
def test_synthetic_sample_forced_observation_only():
    """synthetic_sample=true 的记录必须强制 observation_only=true。"""
    m = _mk_match(synthetic_sample=True)
    rec = write_snapshot(m, _mk_predict())
    assert rec.get("synthetic_sample") is True
    assert rec["observation_only"] is True, "合成样本必须强制 observation_only=true"


# ---- 缺陷5：半全场边际一致性 ----
def test_half_full_marginal_consistency():
    """半全场9项对全场1X2的边际必须一致，否则拒绝写入。

    胜胜+平胜+负胜 应 ≈ p_home，以此类推。
    均匀1/9但全场 .5/.25/.25 是自相矛盾的。
    """
    pred = _mk_predict()
    # 故意构造矛盾的半全场：均匀分布 vs 全场 .5/.25/.25
    pred["derivatives"]["half_full_1x2"] = {k: 1 / 9 for k in
        ["胜胜", "胜平", "胜负", "平胜", "平平", "平负", "负胜", "负平", "负负"]}
    with pytest.raises(ValueError, match="边际"):
        write_snapshot(_mk_match(), pred)
