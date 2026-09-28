# tests/test_trace_batch.py — 批量场景逐场 normalized_payload 追溯信息验证
import json
from datetime import datetime, timezone

import pytest


class TestPerMatchTrace:
    """验证批量场景下每场 normalized_payload 保存完整追溯信息。"""

    def _run_normalize_batch(self, raw_payload):
        import app.normalizer.normalizer as _nmod

        captured_sql = []
        snap_captured_at = datetime(2026, 9, 20, 3, 28, 2, tzinfo=timezone.utc)

        class _C:
            def fetchone(self):
                return None
            def fetchall(self):
                return []

        class _Conn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def transaction(self):
                return self
            def execute(self, sql, params=None):
                captured_sql.append((sql, params))
                return _C()

        class _FakePool:
            def connection(self):
                return _Conn()

        orig_fetch = _nmod._fetch_collection_snapshot
        orig_pool = _nmod.get_pool
        _nmod._fetch_collection_snapshot = lambda sid: {
            "id": sid, "run_id": "run-batch-001", "source": "xiaodianhuo",
            "entity_type": "upload", "external_id": None,
            "raw_payload": raw_payload, "captured_at": snap_captured_at,
        }
        _nmod.get_pool = lambda: _FakePool()
        try:
            results = _nmod.normalize_collection_snapshot("trace-snap-batch-001")
        finally:
            _nmod._fetch_collection_snapshot = orig_fetch
            _nmod.get_pool = orig_pool
        return results, captured_sql

    def test_batch_per_match_trace_ids(self):
        raw = {
            "reportType": "XDH_JCZQ_BATCH",
            "version": "2.0.0",
            "exportedAt": "2026-09-20T03:28:02.374Z",
            "total": 2,
            "processed": 2,
            "matches": [
                {
                    "match": {
                        "match_id": "90001", "match_id2": "",
                        "league": "L1", "home": "A", "away": "B",
                        "match_time": "2026-09-20 13:00:00",
                    },
                    "details": {},
                    "capturedAt": "2026-09-20T03:28:02.374Z",
                },
                {
                    "match": {
                        "match_id": "90002", "match_id2": "",
                        "league": "L1", "home": "C", "away": "D",
                        "match_time": "2026-09-20 15:00:00",
                    },
                    "details": {},
                    "capturedAt": "2026-09-20T03:28:02.374Z",
                },
            ],
        }
        results, captured_sql = self._run_normalize_batch(raw)

        # 提取每场 match_snapshots INSERT 的 normalized_payload
        ms_payloads = []
        for sql, params in captured_sql:
            if params and "INSERT INTO match_snapshots" in sql:
                ms_payloads.append({
                    "match_id": params[1],
                    "collection_run_id": params[2],
                    "normalized": json.loads(params[8]),
                })

        # 两场比赛各产生一条 match_snapshots
        assert len(ms_payloads) == 2, f"expect 2 snapshots, got {len(ms_payloads)}"

        # 每场 normalized_payload 的 _trace 都保存了完整追溯信息
        for p in ms_payloads:
            trace = p["normalized"]["_trace"]
            assert trace["collection_snapshot_id"] == "trace-snap-batch-001", (
                f"trace missing for match {p['match_id']}: {trace}"
            )

        # 逐场区分：不同 match_id
        assert ms_payloads[0]["match_id"] != ms_payloads[1]["match_id"]

        # collection_run_id 逐场正确回填
        for p in ms_payloads:
            assert p["collection_run_id"] == "run-batch-001"
