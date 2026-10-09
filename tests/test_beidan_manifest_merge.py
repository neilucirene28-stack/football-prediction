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
            assert m["scope"] == "full_pool"
            assert m["pool_total"] == 3
            # seq=2 保留最早的（chunk1的skipped版本）
            by_seq = {x["seq"]: x for x in m["matches"]}
            assert by_seq["2"]["status"] == "skipped"
            assert len(m["conflicts"]) == 1
            assert m["conflicts"][0]["kept"] == "old"
        finally:
            if os.path.exists(out):
                os.remove(out)


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


def test_merge_run_id_no_overwrite():
    """同名run_id不覆盖已有文件。"""
    with tempfile.TemporaryDirectory() as td:
        c1 = _write_chunk(td, "m1.json", "26103", "2026-10-09T06:00:00+00:00",
                          [{"seq": "1", "status": "ok"}])
        r1 = _run_merge(td, [c1], "26103", ["--run-id", "test3"])
        r2 = _run_merge(td, [c1], "26103", ["--run-id", "test3"])
        assert r1.returncode == 0 and r2.returncode == 0
        base = os.path.join(os.path.dirname(__file__), "..",
                            "data", "manifests", "beidan", "26103")
        try:
            assert os.path.exists(os.path.join(base, "test3.json"))
            assert os.path.exists(os.path.join(base, "test3_1.json"))
        finally:
            for p in ["test3.json", "test3_1.json"]:
                pp = os.path.join(base, p)
                if os.path.exists(pp):
                    os.remove(pp)


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
            cov = m["coverage"]
            assert cov["schedule_total"] == 3
            assert cov["covered"] == 2
            assert abs(cov["coverage_rate"] - 2 / 3) < 1e-9
            assert ["26103", "3"] in cov["missing_from_merge"] or \
                ("26103", "3") in [tuple(x) for x in cov["missing_from_merge"]]
        finally:
            if os.path.exists(out):
                os.remove(out)
