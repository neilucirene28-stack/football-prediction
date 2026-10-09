"""全池manifest合并测试。"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                      "beidan_manifest_merge.py")


def _write_chunk(tmpdir, name, lottery_no, generated_at, matches):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"lottery_no": lottery_no, "generated_at": generated_at,
                   "matches": matches}, f, ensure_ascii=False)
    return path


def _run_merge(tmpdir, chunks, lottery_no, extra=None, cwd=None):
    out_base = os.path.join(tmpdir, "out")
    # 用环境变量覆盖输出目录：脚本内写死 OUT_BASE，这里改用cwd隔离
    cmd = [sys.executable, SCRIPT, "--chunks"] + chunks + [
        "--lottery-no", lottery_no]
    if extra:
        cmd += extra
    env = dict(os.environ)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                       cwd=cwd or os.path.join(os.path.dirname(__file__), ".."))
    return r


def test_merge_dedup_keeps_earliest():
    """重复(seq)保留generated_at最早的一条，并记录冲突。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"},
                           {"seq": "2", "status": "skipped", "reason": "nodata"}])
        c2 = _write_chunk(td, "m2.json", "26103", "2026-10-09T07:00:00+00:00",
                          [{"seq": "2", "status": "ok"},   # 重复
                           {"seq": "3", "status": "ok"}])
        # 直接调用合并逻辑（避免写data/）：用--run-id并检查输出
        r = _run_merge(td, [c1, c2], "26103", ["--run-id", "test1"])
        assert r.returncode == 0, r.stderr
        out = os.path.join(os.path.dirname(__file__), "..",
                           "data", "manifests", "beidan", "26103", "test1.json")
        try:
            with open(out, encoding="utf-8") as f:
                m = json.load(f)
            assert m["scope"] == "partial_pool"  # 无schedule不得标full_pool
            assert m["pool_total"] == 3
            # seq=2 保留最早的（chunk1的skipped版本）
            by_seq = {x["seq"]: x for x in m["matches"]}
            assert by_seq["2"]["status"] == "skipped"
            assert len(m["conflicts"]) == 1
            assert m["conflicts"][0]["kept"] == "old"
        finally:
            if os.path.exists(out):
                os.remove(out)


def test_scope_partial_without_schedule():
    """无--schedule时scope必须为partial_pool，不能冒充full_pool。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"}])
        r = _run_merge(td, [c1], "26103", ["--run-id", "scope1"])
        assert r.returncode == 0, r.stderr
        out = os.path.join(os.path.dirname(__file__), "..",
                           "data", "manifests", "beidan", "26103", "scope1.json")
        try:
            with open(out, encoding="utf-8") as f:
                m = json.load(f)
            assert m["scope"] == "partial_pool", \
                f"无schedule时scope应为partial_pool，实际={m['scope']}"
            assert m["denominator"] is None
        finally:
            if os.path.exists(out):
                os.remove(out)


def test_scope_partial_with_incomplete_coverage():
    """有schedule但覆盖率<100%时scope必须为partial_pool，并标denominator/coverage。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"}])
        sched = os.path.join(td, "sched.json")
        with open(sched, "w", encoding="utf-8") as f:
            json.dump({"matches": [{"seq": "1"}, {"seq": "2"}, {"seq": "3"},
                                    {"seq": "4"}]}, f)
        r = _run_merge(td, [c1], "26103",
                       ["--run-id", "scope2", "--schedule", sched])
        assert r.returncode == 0, r.stderr
        out = os.path.join(os.path.dirname(__file__), "..",
                           "data", "manifests", "beidan", "26103", "scope2.json")
        try:
            with open(out, encoding="utf-8") as f:
                m = json.load(f)
            assert m["scope"] == "partial_pool", \
                f"覆盖率1/4时scope应为partial_pool，实际={m['scope']}"
            assert m["denominator"] == 4
            assert m["coverage"] == 1
        finally:
            if os.path.exists(out):
                os.remove(out)


def test_scope_full_pool_only_at_100pct():
    """只有schedule提供且覆盖率=100%才能标full_pool。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"},
                           {"seq": "2", "status": "ok"}])
        sched = os.path.join(td, "sched.json")
        with open(sched, "w", encoding="utf-8") as f:
            json.dump({"matches": [{"seq": "1"}, {"seq": "2"}]}, f)
        r = _run_merge(td, [c1], "26103",
                       ["--run-id", "scope3", "--schedule", sched])
        assert r.returncode == 0, r.stderr
        out = os.path.join(os.path.dirname(__file__), "..",
                           "data", "manifests", "beidan", "26103", "scope3.json")
        try:
            with open(out, encoding="utf-8") as f:
                m = json.load(f)
            assert m["scope"] == "full_pool"
        finally:
            if os.path.exists(out):
                os.remove(out)


def test_run_id_rejects_path_traversal():
    """--run-id含路径遍历必须被拒绝，不能写到目录外。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"}])
        evil = os.path.join(td, "evil.json")
        r = _run_merge(td, [c1], "26103", ["--run-id", "../evil"])
        assert r.returncode != 0, "路径遍历run_id应被拒绝"
        assert not os.path.exists(evil), "恶意文件不应被创建"


def test_run_id_rejects_unsafe_chars():
    """--run-id只允许字母数字下划线连字符。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"}])
        for bad in ["a b", "a/b", "a\\b", "a:b", "a*b", ".hidden", "../x"]:
            r = _run_merge(td, [c1], "26103", ["--run-id", bad])
            assert r.returncode != 0, f"run_id={bad!r}应被拒绝"


def test_manual_run_id_collision_errors():
    """手工run_id冲突时必须报错退出，不能静默改名覆盖。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"}])
        base = os.path.join(os.path.dirname(__file__), "..",
                            "data", "manifests", "beidan", "26103")
        r1 = _run_merge(td, [c1], "26103", ["--run-id", "collide1"])
        assert r1.returncode == 0, r1.stderr
        r2 = _run_merge(td, [c1], "26103", ["--run-id", "collide1"])
        try:
            assert r2.returncode != 0, "同名手工run_id第二次应报错退出"
            assert not os.path.exists(os.path.join(base, "collide1_1.json")), \
                "不应静默生成改名文件"
        finally:
            for p in ["collide1.json", "collide1_1.json"]:
                pp = os.path.join(base, p)
                if os.path.exists(pp):
                    os.remove(pp)
def test_merge_wrong_lottery_skipped():
    """期号不一致的chunk被跳过。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"}])
        c2 = _write_chunk(td, "m2.json", "26104", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"}])
        r = _run_merge(td, [c1, c2], "26103", ["--run-id", "test2"])
        assert r.returncode == 0
        assert "不一致" in r.stderr or "跳过" in r.stderr
        out = os.path.join(os.path.dirname(__file__), "..",
                           "data", "manifests", "beidan", "26103", "test2.json")
        try:
            with open(out, encoding="utf-8") as f:
                m = json.load(f)
            assert m["pool_total"] == 1
        finally:
            if os.path.exists(out):
                os.remove(out)


def test_merge_run_id_no_overwrite_removed():
    """旧行为（静默改名test3_1.json）已废弃，见test_manual_run_id_collision_errors。"""
    pass


def test_merge_coverage_against_schedule():
    """对照赛程集合报告覆盖率。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"},
                           {"seq": "2", "status": "ok"}])
        sched = os.path.join(td, "sched.json")
        with open(sched, "w", encoding="utf-8") as f:
            json.dump({"matches": [{"seq": "1"}, {"seq": "2"}, {"seq": "3"}]},
                      f)
        r = _run_merge(td, [c1], "26103",
                       ["--run-id", "test4", "--schedule", sched])
        assert r.returncode == 0, r.stderr
        out = os.path.join(os.path.dirname(__file__), "..",
                           "data", "manifests", "beidan", "26103", "test4.json")
        try:
            with open(out, encoding="utf-8") as f:
                m = json.load(f)
            cov = m["coverage_detail"]
            assert cov["schedule_total"] == 3
            assert cov["covered"] == 2
            assert abs(cov["coverage_rate"] - 2 / 3) < 1e-9
            assert ["26103", "3"] in cov["missing_from_merge"] or \
                ("26103", "3") in [tuple(x) for x in cov["missing_from_merge"]]
        finally:
            if os.path.exists(out):
                os.remove(out)
