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
    # 半全场9类：边际必须与全场1X2一致（GPT审计要求）
    # 全场胜=胜胜+平胜+负胜，以此类推
    hf = {
        "胜胜": ph * 0.6, "胜平": pd * 0.4, "胜负": pa * 0.4,
        "平胜": ph * 0.25, "平平": pd * 0.35, "平负": pa * 0.25,
        "负胜": ph * 0.15, "负平": pd * 0.25, "负负": pa * 0.35,
    }
    # 归一化到和为1（保持边际比例）
    s = sum(hf.values())
    hf = {k: v / s for k, v in hf.items()}
    # 按边际缩放以精确匹配 p_1x2
    # 先算当前边际
    m_h = hf["胜胜"] + hf["平胜"] + hf["负胜"]
    m_d = hf["胜平"] + hf["平平"] + hf["负平"]
    m_a = hf["胜负"] + hf["平负"] + hf["负负"]
    # 缩放每列
    for k in ("胜胜", "平胜", "负胜"):
        hf[k] = hf[k] / m_h * ph if m_h else 0
    for k in ("胜平", "平平", "负平"):
        hf[k] = hf[k] / m_d * pd if m_d else 0
    for k in ("胜负", "平负", "负负"):
        hf[k] = hf[k] / m_a * pa if m_a else 0
    return {
        "p_home": ph, "p_draw": pd, "p_away": pa,
        "p_final_full": [ph, pd, pa],
        "lambda_home": lam_h, "lambda_away": lam_a,
        "lambda_home_full": lam_h, "lambda_away_full": lam_a,
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
    """并发安全写入：必须用 O_APPEND + fcntl独占锁 + fsync（GPT审计要求）。

    旧的 read-all + os.replace 模式在并发下丢记录，已废弃。
    """
    import inspect
    # 实现已拆分为 write_snapshot（统计wrapper）+ _write_snapshot_impl
    src = inspect.getsource(bs._write_snapshot_impl)
    assert "O_APPEND" in src, "应使用 O_APPEND 追加模式"
    assert "flock" in src, "应使用 fcntl 文件锁"
    assert "fsync" in src, "应调用fsync"
    # 不得再使用 read-all + replace 模式
    assert "old.read()" not in src, "不得使用 read-all + replace 模式"


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


# ============ SP无采集时间漏洞测试（P0追加）============

def test_sp_values_without_collection_evidence_marks_observation_only(tmp_path, monkeypatch):
    """SP数值存在但collected_at=null、available_at只有form_source（无sp/odds源）
    → 必须 observation_only=true（漏洞：原检查只在collected_at为真时触发）。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()
    now = datetime.now(timezone.utc).isoformat()
    md = _mock_match_data(
        seq="sp1",
        available_at={"espn": now},  # 只有战绩源，无SP源
        sp_snapshot={
            "sp_wdl": {"胜": 2.0, "平": 3.0, "负": 4.0},  # SP数值存在
            "collected_at": None,  # 但无采集时间
        },
    )
    r = bs.write_snapshot(md, pr)
    assert r["observation_only"] is True, \
        "SP数值参与预测但无采集时间证据，必须标observation_only"
    assert "sp" in r["observation_reason"].lower() or "SP" in r["observation_reason"]


def test_sp_with_real_evidence_not_observation_only(tmp_path, monkeypatch):
    """SP有真实采集证据（available_at含sp源+有效时间）→ 可observation_only=false。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()
    now = datetime.now(timezone.utc).isoformat()
    md = _mock_match_data(
        seq="sp2",
        available_at={"espn": now, "okooo_sp": now},  # 有SP源
        sp_snapshot={
            "sp_wdl": {"胜": 2.0, "平": 3.0, "负": 4.0},
            "collected_at": now,  # 有采集时间
        },
    )
    r = bs.write_snapshot(md, pr)
    assert r["observation_only"] is False


def test_no_sp_values_no_sp_check(tmp_path, monkeypatch):
    """无SP数值时不触发SP检查（不误伤）。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    pr = _mock_predict_result()
    md = _mock_match_data(seq="sp3", sp_snapshot=None)
    r = bs.write_snapshot(md, pr)
    assert r["observation_only"] is False


# ============ 真实predict集成测试（GPT追加要求）============

def test_integration_real_predict_write_snapshot(tmp_path, monkeypatch):
    """用真实 predict(payload, model='beidan')（含真实让球线），
    验证 write_snapshot 成功及六玩法一致性。

    关键：predictor的derivatives做了round(v,4)，快照必须用未舍入全精度，
    否则概率和/边际校验（1e-6）会失败导致写入被拒。
    """
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    bs.reset_write_stats()
    from engine.predictor import predict

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "home": "阿森纳", "away": "切尔西",
        "kickoff_at": "2026-10-10T19:00:00+08:00",
        "snapshot_at": now,
        "competition": "英超",
        "home_recent": [{"gf": 2, "ga": 1, "venue": "H"} for _ in range(10)],
        "away_recent": [{"gf": 1, "ga": 1, "venue": "A"} for _ in range(10)],
        "handicap": -1,
        "handicap_line": -1,
        "league_avg_goals": 2.70,
    }
    res = predict(payload, model="beidan")
    assert res.get("status") != "insufficient_data", "测试payload应能预测"

    # 验证predictor输出了未舍入全精度字段
    h1x2 = res["derivatives"]["handicap_1x2"]
    assert "p_home_full" in h1x2, "predictor应输出未舍入的p_home_full"
    assert "half_full_1x2_full" in res["derivatives"], \
        "predictor应输出未舍入的half_full_1x2_full"

    md = _mock_match_data(
        seq="integ1",
        # 来源门控：handicap_line=-1参与预测，必须有让球源时间证据
        available_at={"espn": now, "handicap_line": now},
        handicap_line=-1,
    )
    # 真实写入，不应因舍入误差被拒
    r = bs.write_snapshot(md, res)
    assert r["observation_only"] is False

    # 六玩法一致性：每类概率和≈1（1e-6）
    six = r["six_play_vector"]
    for name, vec in [("wdl", six["wdl"]),
                      ("score_31", six["score_31"]),
                      ("total_goals", six["total_goals"]),
                      ("half_full", six["half_full"]),
                      ("ou", six["ou"])]:
        s = sum(vec.values())
        assert abs(s - 1.0) < 1e-6, f"{name}概率和={s}，偏离1超过1e-6"
    hw = six["handicap_wdl"]
    assert hw is not None
    s_hw = hw["让胜"] + hw["让平"] + hw["让负"]
    assert abs(s_hw - 1.0) < 1e-6, f"handicap_wdl概率和={s_hw}"

    # 半全场边际 = 全场1X2
    hf = six["half_full"]
    assert abs((hf["胜胜"] + hf["平胜"] + hf["负胜"]) - r["p_1x2"][0]) < 1e-6
    assert abs((hf["胜平"] + hf["平平"] + hf["负平"]) - r["p_1x2"][1]) < 1e-6
    assert abs((hf["胜负"] + hf["平负"] + hf["负负"]) - r["p_1x2"][2]) < 1e-6

    # 统计
    stats = bs.get_write_stats()
    assert stats["success"] >= 1
    assert stats["failed"] == 0


def test_write_failure_counted(tmp_path, monkeypatch):
    """写入失败要计数，不能静默丢（影响分母统计）。"""
    monkeypatch.setattr(bs, "SNAPSHOT_DIR", str(tmp_path))
    bs.reset_write_stats()
    pr = _mock_predict_result()
    # 构造必失败的输入：缺少score_matrix_full
    bad_pr = dict(pr)
    bad_pr["score_matrix_full"] = None
    md = _mock_match_data(seq="fail1")
    try:
        bs.write_snapshot(md, bad_pr)
        assert False, "应抛出异常"
    except ValueError:
        pass
    stats = bs.get_write_stats()
    assert stats["failed"] == 1, "失败必须计数"
    assert stats["success"] == 0
