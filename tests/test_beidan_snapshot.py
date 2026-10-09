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


def test_31class_covers_all():
    """31类 = 13胜+5平+13负，含三类'其他'（福建体彩2024-04-01规则）。"""
    assert len(bs.BEIDAN_SCORE_31["win"]) == 13
    assert len(bs.BEIDAN_SCORE_31["draw"]) == 5
    assert len(bs.BEIDAN_SCORE_31["lose"]) == 13
    assert "胜其他" in bs.BEIDAN_SCORE_31["win"]
    assert "平其他" in bs.BEIDAN_SCORE_31["draw"]
    assert "负其他" in bs.BEIDAN_SCORE_31["lose"]


def test_aggregate_31class_sums_to_one_v2():
    m = score_matrix(1.8, 1.2, rho=-0.13)
    mc = ipf_to_marginals(m, list(match_probs(m)))
    dist = bs.aggregate_31class(mc)
    assert len(dist) == 31
    assert abs(sum(dist.values()) - 1.0) < 1e-6


def test_six_play_vector_completeness():
    """六玩法每类概率和为1。"""
    pr = _mock_predict_result()
    six = bs.build_six_play_vector(pr)
    assert abs(sum(six["wdl"].values()) - 1.0) < 1e-3
    hw = six["handicap_wdl"]
    assert abs(hw["让胜"] + hw["让平"] + hw["让负"] - 1.0) < 1e-3
    assert abs(sum(six["score_31"].values()) - 1.0) < 1e-6
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
    # 修正：第二条rerun_of必须指向实际存在的第一条"26103:1"，不是不存在的#r1
    assert r2["rerun_of"] == "26103:1"
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


# ============ P0审计缺陷针对性测试（先失败，后修复）============

def test_31class_lose_has_13():
    """31类：客胜13类（含0-4,1-4,2-4,0-5,1-5,2-5,负其他）。"""
    assert len(bs.BEIDAN_SCORE_31["win"]) == 13
    assert len(bs.BEIDAN_SCORE_31["draw"]) == 5
    assert len(bs.BEIDAN_SCORE_31["lose"]) == 13
    assert "负其他" in bs.BEIDAN_SCORE_31["lose"]
    # 25类误作全场时缺失的比分
    for s in ["0-4", "1-4", "2-4", "0-5", "1-5", "2-5"]:
        assert s in bs.BEIDAN_SCORE_31["lose"], f"{s}应在31类客胜中"


def test_31class_mapping():
    """31类映射：0-4/2-5等应映射到具体类而非负其他。"""
    assert bs.score_to_beidan_class(0, 4) == "0-4"
    assert bs.score_to_beidan_class(2, 5) == "2-5"
    assert bs.score_to_beidan_class(1, 5) == "1-5"
    assert bs.score_to_beidan_class(0, 6) == "负其他"
    assert bs.score_to_beidan_class(6, 0) == "胜其他"
    assert bs.score_to_beidan_class(4, 4) == "平其他"

def test_naive_timestamp_rejected():
    """无时区时间戳必须被拒绝（不能强行按UTC）。"""
    assert bs._has_real_time_evidence("2026-10-09T14:20:16") is False
    assert bs._has_real_time_evidence("2026-10-09 14:20:16") is False
    # 有时区的合法过去时间 → True
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert bs._has_real_time_evidence(past) is True


def test_null_source_not_ignored(tmp_path, monkeypatch):
    """available_at中null来源不能被all()忽略，必须标observation_only。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()
    now = datetime.now(timezone.utc).isoformat()
    md = _mock_match_data(available_at={"espn": now, "7m": None})
    r = bs.write_snapshot(md, pr)
    assert r["observation_only"] is True, "null来源必须显式标记，不能忽略"


def test_time_ordering_violations(tmp_path, monkeypatch):
    """时间顺序 available_at <= asof <= generated_at < kickoff，违反则observation_only。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()
    now = datetime.now(timezone.utc)
    past = (now - timedelta(hours=2)).isoformat()
    future_kickoff = (now + timedelta(hours=2)).isoformat()

    # available_at 晚于 generated_at → 违反
    md = _mock_match_data(
        seq="10",
        generated_at=past,
        available_at={"espn": now.isoformat()},
        kickoff=future_kickoff,
    )
    r = bs.write_snapshot(md, pr)
    assert r["observation_only"] is True, "available_at>generated_at应标observation_only"

    # generated_at 晚于 kickoff → 违反（赛后生成的不能算赛前）
    md2 = _mock_match_data(
        seq="11",
        generated_at=now.isoformat(),
        available_at={"espn": past},
        kickoff=past,  # 开球早于生成
    )
    r2 = bs.write_snapshot(md2, pr)
    assert r2["observation_only"] is True, "generated_at>kickoff应标observation_only"


def test_prob_sum_tolerance_1e6():
    """容差必须1e-6（与文档一致），1e-3太松。"""
    borderline = {"a": 0.50025, "b": 0.50025}  # 和=1.0005，在1e-3内但超1e-6
    try:
        bs._check_prob_sum(borderline, "test")
        assert False, "和偏离0.0005应在1e-6容差下被拒绝"
    except ValueError:
        pass


def test_prob_range_and_finite():
    """各项必须0<=p<=1且有限（非NaN/Inf）。"""
    import math
    for bad_dist, name in [
        ({"a": 1.5, "b": -0.5}, "超范围"),
        ({"a": float("nan"), "b": 1.0}, "NaN"),
        ({"a": float("inf"), "b": 0.0}, "Inf"),
    ]:
        try:
            bs._check_prob_sum(bad_dist, name)
            assert False, f"{name}应被拒绝"
        except ValueError:
            pass


def test_rerun_of_points_to_existing(tmp_path, monkeypatch):
    """第二次重跑的rerun_of必须指向实际存在的第一条（无#r1）。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()
    md = _mock_match_data()

    r1 = bs.write_snapshot(md, pr)
    r2 = bs.write_snapshot(md, pr)
    r3 = bs.write_snapshot(md, pr)

    # 第一条无后缀
    assert r1["snapshot_id"] == "26103:1"
    assert r1["rerun_of"] is None
    # 第二条必须指向实际存在的第一条
    assert r2["rerun_of"] == "26103:1", f"第二条rerun_of应为26103:1，实际{r2['rerun_of']}"
    # 第三条指向第二条
    assert r3["rerun_of"] == r2["snapshot_id"]

    # 文件中所有rerun_of都必须对应已存在的snapshot_id
    path = tmp_path / "26103.jsonl"
    ids = set()
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        rec = json.loads(line)
        if rec["rerun_of"] is not None:
            assert rec["rerun_of"] in ids, f"rerun_of {rec['rerun_of']} 不存在"
        ids.add(rec["snapshot_id"])


def test_atomic_write_uses_temp_and_fsync(tmp_path, monkeypatch):
    """原子写入：必须用临时文件+rename+fsync。"""
    import inspect
    src = inspect.getsource(bs.write_snapshot)
    assert "mkstemp" in src or "NamedTemporaryFile" in src, "应使用临时文件"
    assert "os.replace" in src or "os.rename" in src, "应使用原子rename"
    assert "fsync" in src, "应调用fsync"


def test_handicap_missing_returns_none():
    """让球线缺失时统一返回None，不返回零概率/None混合dict。"""
    pr = _mock_predict_result()
    pr["derivatives"]["handicap_1x2"] = None  # 无让球数据
    six = bs.build_six_play_vector(pr)
    hw = six["handicap_wdl"]
    # 要么完整三元组，要么整体None，不允许{"让胜":0.0,...,"handicap_line":None}混合
    if hw is None:
        pass  # 整体None，可接受
    else:
        assert hw["handicap_line"] is not None, "有dict就必须有line"
        for k in ("让胜", "让平", "让负"):
            assert hw[k] is not None
