"""北单快照链路测试（engine/beidan_snapshot.py）。

覆盖：
1. append-only：重复写入不覆盖，rerun_of链正确
2. 时间证据缺失 → observation_only=true
3. 六玩法向量完整性（每类概率和为1）
4. 25类比分含胜其他/平其他/负其他
5. 比分映射正确性（具体比分→25类）
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engine.beidan_snapshot as bs
from engine.poisson import score_matrix, ipf_to_marginals, match_probs


def _mock_predict_result(lam_h=1.8, lam_a=1.2):
    """构造最小可用的predict()结果（含score_matrix_full）。"""
    m = score_matrix(lam_h, lam_a, rho=-0.13)
    ph, pd, pa = match_probs(m)
    mc = ipf_to_marginals(m, [ph, pd, pa])
    # 让球1x2（简化：用矩阵直接算）
    from engine.poisson import handicap_1x2
    h, d, a = handicap_1x2(mc, 0)
    # 半全场9类（均匀占位，测试用）
    hf = {k: 1/9 for k in bs.HALF_FULL_9}
    return {
        "p_home": ph, "p_draw": pd, "p_away": pa,
        "lambda_home": lam_h, "lambda_away": lam_a,
        "model_version": "2.10",
        "score_matrix_full": mc,
        "derivatives": {
            "handicap_1x2": {"line": 0, "p_home": h, "p_draw": d, "p_away": a},
            "top_scores": [{"score": "1-1", "prob": 0.1}],
            "half_full_1x2": hf,
        },
    }


def _mock_match_data(**kw):
    now = datetime.now(timezone.utc).isoformat()
    d = {
        "lottery_no": "26103",
        "seq": "1",
        "league": "测试联赛",
        "home": "主队A",
        "away": "客队B",
        "kickoff": "2026-10-10T19:00:00+08:00",
        "generated_at": now,
        "available_at": {"espn": now},
    }
    d.update(kw)
    return d


def _tmp_dir(monkeypatch=None):
    """隔离的快照目录。"""
    td = tempfile.mkdtemp()
    return td


def test_score_to_beidan_class():
    assert bs.score_to_beidan_class(1, 0) == "1-0"
    assert bs.score_to_beidan_class(2, 1) == "2-1"
    assert bs.score_to_beidan_class(0, 0) == "0-0"
    assert bs.score_to_beidan_class(1, 1) == "1-1"
    assert bs.score_to_beidan_class(0, 2) == "0-2"
    # 超出列表 → 其他类
    assert bs.score_to_beidan_class(6, 0) == "胜其他"
    assert bs.score_to_beidan_class(4, 4) == "平其他"
    assert bs.score_to_beidan_class(0, 6) == "负其他"
    assert bs.score_to_beidan_class(5, 3) == "胜其他"  # 5-3不在13类内


def test_25class_covers_all():
    """25类 = 13胜+5平+7负，含三类'其他'。"""
    assert len(bs.BEIDAN_SCORE_25["win"]) == 13
    assert len(bs.BEIDAN_SCORE_25["draw"]) == 5
    assert len(bs.BEIDAN_SCORE_25["lose"]) == 7
    assert "胜其他" in bs.BEIDAN_SCORE_25["win"]
    assert "平其他" in bs.BEIDAN_SCORE_25["draw"]
    assert "负其他" in bs.BEIDAN_SCORE_25["lose"]


def test_aggregate_25class_sums_to_one():
    m = score_matrix(1.8, 1.2, rho=-0.13)
    mc = ipf_to_marginals(m, list(match_probs(m)))
    dist = bs.aggregate_25class(mc)
    assert len(dist) == 25
    assert abs(sum(dist.values()) - 1.0) < 1e-6


def test_six_play_vector_completeness():
    """六玩法每类概率和为1。"""
    pr = _mock_predict_result()
    six = bs.build_six_play_vector(pr)
    assert abs(sum(six["wdl"].values()) - 1.0) < 1e-3
    hw = six["handicap_wdl"]
    assert abs(hw["让胜"] + hw["让平"] + hw["让负"] - 1.0) < 1e-3
    assert abs(sum(six["score_25"].values()) - 1.0) < 1e-6
    assert abs(sum(six["total_goals"].values()) - 1.0) < 1e-6
    assert abs(sum(six["half_full"].values()) - 1.0) < 1e-3
    assert abs(sum(six["ou"].values()) - 1.0) < 1e-6
    # score_top5仅展示
    assert "score_top5_display" in six


def test_write_snapshot_append_only(tmp_path, monkeypatch):
    """重复写入不覆盖，rerun_of链正确。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()
    md = _mock_match_data()

    r1 = bs.write_snapshot(md, pr)
    r2 = bs.write_snapshot(md, pr)  # 重跑

    assert r1["rerun_of"] is None
    assert r2["rerun_of"] == "26103:1#r1"
    assert r1["snapshot_id"] == "26103:1"
    assert r2["snapshot_id"] == "26103:1#r2"

    # 文件有两行，第一行未被覆盖
    path = tmp_path / "26103.jsonl"
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    rec1 = json.loads(lines[0])
    assert rec1["snapshot_id"] == "26103:1"
    assert rec1["rerun_of"] is None


def test_missing_time_marks_observation_only(tmp_path, monkeypatch):
    """时间证据缺失 → observation_only=true。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()

    # generated_at缺失
    md = _mock_match_data(generated_at=None)
    r = bs.write_snapshot(md, pr)
    assert r["observation_only"] is True
    assert r["provenance_unverified"] is True

    # available_at缺失
    md2 = _mock_match_data(seq="2", available_at={})
    r2 = bs.write_snapshot(md2, pr)
    assert r2["observation_only"] is True

    # 未来时间戳（预设）→ 拒绝为有效证据
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    md3 = _mock_match_data(seq="3", generated_at=future)
    r3 = bs.write_snapshot(md3, pr)
    assert r3["observation_only"] is True


def test_valid_time_not_observation_only(tmp_path, monkeypatch):
    """时间证据齐全 → observation_only=false。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()
    md = _mock_match_data()
    r = bs.write_snapshot(md, pr)
    assert r["observation_only"] is False
    assert r["provenance_unverified"] is False


def test_prob_sum_violation_rejected():
    """概率和偏离1 → 拒绝写入。"""
    bad = {"a": 0.5, "b": 0.3}  # 和=0.8
    try:
        bs._check_prob_sum(bad, "test")
        assert False, "应抛出ValueError"
    except ValueError as e:
        assert "概率和" in str(e)
