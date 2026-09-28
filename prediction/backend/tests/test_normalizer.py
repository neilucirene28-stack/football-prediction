# tests/test_normalizer.py — Normalizer V1 自动化测试（纯函数，不连接数据库）
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.normalizer.canonical import beijing_str_to_utc, beijing_to_utc
from app.normalizer.adapters.titan007 import adapt_titan007
from app.normalizer.adapters.xiaodianhuo import adapt_xiaodianhuo_batch


SAMPLES_DIR = Path("/opt/football-prediction/data/samples")

# ========== 时区转换 ==========

class TestTimezone:
    def test_beijing_to_utc_20260920_1957(self):
        """2026-09-20 19:57 北京时间 → 2026-09-20 11:57 UTC"""
        dt = datetime(2026, 9, 20, 19, 57)
        utc = beijing_to_utc(dt)
        assert utc.hour == 11
        assert utc.minute == 57
        assert utc.tzinfo is not None

    def test_beijing_str_to_utc_1957(self):
        utc = beijing_str_to_utc("2026-09-20 19:57", "%Y-%m-%d %H:%M")
        assert utc.hour == 11
        assert utc.minute == 57

    def test_result_is_timezone_aware(self):
        utc = beijing_str_to_utc("2026-09-20 19:57", "%Y-%m-%d %H:%M")
        assert utc.tzinfo is not None

    def test_slash_format(self):
        utc = beijing_str_to_utc("2026/09/20 19:58", "%Y/%m/%d %H:%M")
        assert utc.hour == 11

# ========== Titan007 适配器 ==========


class TestTitan007Adapter:
    @pytest.fixture
    def sample(self):
        p = SAMPLES_DIR / "titan007_fulham_vs_manutd.json"
        if not p.exists():
            pytest.skip("sample not found")
        return json.load(open(p, encoding="utf-8"))

    def test_ext_id(self, sample):
        cm = adapt_titan007(sample)
        assert cm.external_match_id == "3003893"

    def test_kickoff_unknown(self, sample):
        cm = adapt_titan007(sample)
        assert cm.match.kickoff_known is False
        assert cm.match.kickoff_at is None

    def test_competition_not_guessed(self, sample):
        cm = adapt_titan007(sample)
        assert cm.match.competition is None

    def test_asia_odds_counts(self, sample):
        cm = adapt_titan007(sample)
        opening = sum(1 for l in cm.asia_odds if l.is_opening is True)
        live = sum(1 for l in cm.asia_odds if l.is_opening is False)
        history = sum(1 for l in cm.asia_odds if l.is_opening is None)
        assert opening == 45
        assert live == 45
        assert history == 201

    def test_history_observed_at_is_utc_aware(self, sample):
        cm = adapt_titan007(sample)
        history = [l for l in cm.asia_odds if l.is_opening is None]
        assert all(l.observed_at is not None for l in history)
        assert all(l.observed_at.tzinfo is not None for l in history)

    def test_history_time_semantics(self, sample):
        cm = adapt_titan007(sample)
        history = [l for l in cm.asia_odds if l.is_opening is None]
        assert all(l.time_semantics == "change_time" for l in history)

    def test_corner_opening_live_separate(self, sample):
        """角球初盘与即时盘必须分开为独立记录"""
        cm = adapt_titan007(sample)
        opening = [l for l in cm.corner_odds if l.is_opening is True]
        live = [l for l in cm.corner_odds if l.is_opening is False]
        assert len(opening) >= 1
        assert len(live) >= 1

    def test_flattened_tables_detected(self, sample):
        """扁平化表格按表格编号逐个检出，不能整类报废"""
        cm = adapt_titan007(sample)
        error_tables = {
            (b.table_name, b.table_no)
            for b in cm.table_blocks if b.quality == "parse_error"
        }
        assert ("近期战绩", 23) not in error_tables  # 表格23正常
        assert ("近期战绩", 51) in error_tables
        assert ("近期战绩", 52) in error_tables
        assert ("近期战绩", 54) in error_tables
        assert ("伤停信息", 36) in error_tables
        assert ("伤停信息", 37) in error_tables
        assert ("阵容信息", 40) in error_tables

    def test_normal_table_preserved(self, sample):
        """正常表格（近期战绩.23）应保留为 ok 状态"""
        cm = adapt_titan007(sample)
        ok_tables = {
            (b.table_name, b.table_no)
            for b in cm.table_blocks if b.quality == "ok"
        }
        assert ("近期战绩", 23) in ok_tables

    def test_over_under_not_collected_once(self, sample):
        cm = adapt_titan007(sample)
        ou = [m for m in cm.module_states if m.name == "over_under"]
        assert len(ou) == 1
        assert ou[0].quality == "not_collected"

    def test_europe_empty_flag(self, sample):
        cm = adapt_titan007(sample)
        eu = [m for m in cm.module_states if m.name == "europe" and m.quality == "empty"]
        assert len(eu) == 1
        assert "fetch_ok_but_data_empty" in (eu[0].detail or "")

    def test_collected_at_timezone_aware(self, sample):
        cm = adapt_titan007(sample)
        assert cm.collected_at is not None
        assert cm.collected_at.tzinfo is not None

# ========== 小店欢适配器 ==========


class TestXiaodianhuoAdapter:
    @pytest.fixture
    def sample(self):
        p = SAMPLES_DIR / "xiaodianhuo_batch.json"
        if not p.exists():
            pytest.skip("sample not found")
        return json.load(open(p, encoding="utf-8"))

    def test_batch_count(self, sample):
        cms = adapt_xiaodianhuo_batch(sample)
        assert len(cms) == 31

    def test_match_3_incheon(self, sample):
        cms = adapt_xiaodianhuo_batch(sample)
        cm = cms[3]
        assert cm.external_match_id == "1260920004"
        assert cm.match.competition == "韩职"
        assert cm.match.home_team == "仁川联"
        assert cm.match.away_team == "大田市民"

    def test_kickoff_timezone_aware(self, sample):
        cms = adapt_xiaodianhuo_batch(sample)
        cm = cms[3]
        assert cm.match.kickoff_known is True
        assert cm.match.kickoff_at.tzinfo is not None
        # 北京时间 18:00 → UTC 10:00
        assert cm.match.kickoff_at.hour == 10

    def test_sporttery_odds_mapping(self, sample):
        cms = adapt_xiaodianhuo_batch(sample)
        cm = cms[3]
        wdl = [o for o in cm.sporttery_odds if o.market == "wdl"]
        assert len(wdl) == 1
        assert wdl[0].home_odds == 1.51
        assert wdl[0].draw_odds == 3.85
        assert wdl[0].away_odds == 4.85

    def test_details_fetch_failed(self, sample):
        cms = adapt_xiaodianhuo_batch(sample)
        cm = cms[3]
        failed = [m for m in cm.module_states if m.quality == "fetch_failed"]
        assert len(failed) == 3
        assert all("HTTP 404" in (m.fetch_reason or "") for m in failed)

    def test_fifa_rank_preserved(self, sample):
        cms = adapt_xiaodianhuo_batch(sample)
        cm = cms[3]
        assert "1" in cm.fifa_rank
        assert cm.fifa_rank["1"]["team_name"] == "仁川联"

    def test_collected_at_timezone_aware(self, sample):
        cms = adapt_xiaodianhuo_batch(sample)
        for cm in cms:
            if cm.collected_at:
                assert cm.collected_at.tzinfo is not None


# ========== 解包逻辑测试（有 _ingest / 缺 data / 无 _ingest）==========
import pytest as _pytest


class TestUnwrapPayload:
    """验证 normalize_collection_snapshot 的 payload 解包行为（不连接数据库）。"""

    def _run_normalize(self, raw_payload):
        import app.normalizer.normalizer as _nmod
        from app.normalizer.identity import IdentityResult as _IR

        orig_fetch = _nmod._fetch_collection_snapshot
        orig_resolve = _nmod.resolve_match
        orig_create = _nmod._create_match_snapshot
        orig_pool = _nmod.get_pool

        class _FakeConn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def transaction(self):
                return self
            def execute(self, *a, **kw):
                class _C:
                    def fetchone(self):
                        return None
                return _C()

        class _FakePool:
            def connection(self):
                return _FakeConn()

        _nmod._fetch_collection_snapshot = lambda sid: {
            "id": sid, "run_id": None, "source": "titan007",
            "entity_type": "upload", "external_id": "test_id",
            "raw_payload": raw_payload, "captured_at": None,
        }
        _nmod.resolve_match = lambda conn, c: _IR(
            "pending_review", pending_reason="test-only")
        _nmod.get_pool = lambda: _FakePool()
        try:
            return _nmod.normalize_collection_snapshot("test-snap-id")
        finally:
            _nmod._fetch_collection_snapshot = orig_fetch
            _nmod.resolve_match = orig_resolve
            _nmod._create_match_snapshot = orig_create
            _nmod.get_pool = orig_pool

    def test_normal_structure_unwraps_data(self):
        raw = {
            "_ingest": {"sha256": "x"},
            "data": {"match_info": {"id": "999"}},
        }
        results = self._run_normalize(raw)
        assert len(results) == 1
        assert results[0].action == "pending_review"

    def test_ingest_without_data_raises(self):
        raw = {"_ingest": {"sha256": "x"}}
        with _pytest.raises(Exception, match="missing data"):
            self._run_normalize(raw)

    def test_no_ingest_keeps_payload(self):
        raw = {"match_info": {"id": "888"}}
        results = self._run_normalize(raw)
        assert len(results) == 1
        assert results[0].action == "pending_review"


class TestSnapshotTrace:
    """验证 collection_snapshot_id 被持久化写入 normalized_payload。"""

    def test_trace_id_written(self):
        import app.normalizer.normalizer as _nmod

        captured = {}

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
                captured['sql'] = sql
                captured['params'] = params
                return _C()

        conn = _Conn()
        ms_id = _nmod._create_match_snapshot(
            conn,
            collection_snapshot_id='snap-abc-123',
            collection_run_id='run-xyz-789',
            match_id='match-001',
            source='titan007',
            external_match_id='3003893',
            collected_at=datetime(2026, 9, 20, 11, 57, tzinfo=timezone.utc),
            raw_payload={'k': 'v'},
            completeness={'asia': 'complete'},
        )
        assert ms_id
        assert 'match_snapshots' in captured['sql']
        # params: (ms_id, match_id, collection_run_id, source, collected_at,
        #          data_status, completeness_json, raw_payload_json, normalized_json)
        normalized = json.loads(captured['params'][8])
        assert normalized['_trace']['collection_snapshot_id'] == 'snap-abc-123'


class TestCollectedAtFallback:
    """验证采集时间回退：优先快照 captured_at，不用执行时刻 now()。"""

    def test_captured_at_from_snapshot(self):
        import app.normalizer.normalizer as _nmod
        from app.normalizer.identity import IdentityResult as _IR

        captured = {}
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
                if 'collection_snapshots' in str(sql) or 'INSERT INTO match_snapshots' in str(sql):
                    captured['ms_params'] = params
                return _C()

        # 数据内无 capturedAt（cm.collected_at 为 None）→ 必须回退到快照 captured_at
        entry = {
            'match': {
                'match_id': '9990001', 'match_id2': '',
                'league': '测试联赛', 'home': '主队', 'away': '客队',
                'match_time': '2026-09-20 13:00:00',
            },
            'details': {},
            # 故意不含 capturedAt
        }
        raw = {
            'reportType': 'XDH_JCZQ_BATCH', 'version': '2.0.0',
            'exportedAt': None, 'total': 1, 'processed': 1,
            'matches': [entry],
        }

        orig_fetch = _nmod._fetch_collection_snapshot
        orig_pool = _nmod.get_pool
        _nmod._fetch_collection_snapshot = lambda sid: {
            'id': sid, 'run_id': 'run-1', 'source': 'xiaodianhuo',
            'entity_type': 'upload', 'external_id': '9990001',
            'raw_payload': raw, 'captured_at': snap_captured_at,
        }
        _nmod.get_pool = lambda: _FakePoolEx(conn=_Conn())
        try:
            results = _nmod.normalize_collection_snapshot('snap-time-test')
        finally:
            _nmod._fetch_collection_snapshot = orig_fetch
            _nmod.get_pool = orig_pool

        assert len(results) == 1
        r = results[0]
        assert r.action == 'created'
        assert r.match_id

        # 验证 match_snapshots 的 collected_at 参数 = 快照 captured_at（非 now()）
        ms_params = captured.get('ms_params')
        assert ms_params is not None, 'match_snapshots INSERT 未被捕获'
        collected = ms_params[4]
        assert collected == snap_captured_at, (
            f'collected_at 应为快照采集时间 {snap_captured_at}，'
            f'实际 {collected}'
        )


class _FakePoolEx:
    def __init__(self, conn):
        self._conn = conn
    def connection(self):
        return self._conn


class TestSnapshotTraceV2:
    """验证 normalized_payload 保存完整追溯与业务信息。"""

    def test_trace_and_business_info_written(self):
        import app.normalizer.normalizer as _nmod

        captured = {}

        class _C:
            def fetchone(self):
                return None

        class _Conn:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def transaction(self):
                return self
            def execute(self, sql, params=None):
                captured['sql'] = sql
                captured['params'] = params
                return _C()

        completeness = {'asia': 'complete', 'europe': 'empty'}
        _nmod._create_match_snapshot(
            _Conn(),
            collection_snapshot_id='snap-abc-123',
            collection_run_id='run-xyz-789',
            match_id='match-001',
            source='titan007',
            external_match_id='3003893',
            collected_at=datetime(2026, 9, 20, 11, 57, tzinfo=timezone.utc),
            raw_payload={'k': 'v'},
            completeness=completeness,
        )
        normalized = json.loads(captured['params'][8])
        # 追溯字段
        assert normalized['_trace']['collection_snapshot_id'] == 'snap-abc-123'
        # 业务字段
        assert normalized['_trace']['external_match_id'] == '3003893'
        assert normalized['completeness'] == completeness


class TestCollectedAtMissingGuard:
    """方案 A：采集时间双重缺失保护测试。"""

    def _run_normalize(self, raw_payload, snap_captured_at=None):
        import app.normalizer.normalizer as _nmod
        from app.normalizer.identity import IdentityResult as _IR

        calls = {"resolve": 0, "ms_create": 0}
        snap_captured_at = snap_captured_at

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
                if 'INSERT INTO match_snapshots' in str(sql):
                    calls['ms_create'] += 1
                return _C()

        class _FakePool:
            def connection(self):
                return _Conn()

        orig_fetch = _nmod._fetch_collection_snapshot
        orig_pool = _nmod.get_pool
        orig_resolve = _nmod.resolve_match
        _nmod._fetch_collection_snapshot = lambda sid: {
            'id': sid, 'run_id': 'run-1', 'source': 'xiaodianhuo',
            'entity_type': 'upload', 'external_id': '9990001',
            'raw_payload': raw_payload, 'captured_at': snap_captured_at,
        }
        _nmod.get_pool = lambda: _FakePool()
        _nmod.resolve_match = lambda conn, c: _IR(
            'pending_review', pending_reason='test-only')
        try:
            return _nmod.normalize_collection_snapshot('snap-guard-test')
        finally:
            _nmod._fetch_collection_snapshot = orig_fetch
            _nmod.get_pool = orig_pool
            _nmod.resolve_match = orig_resolve

    def test_both_missing_returns_pending_review(self):
        # 双重缺失：快照 captured_at=None + 数据内 capturedAt 缺失
        entry = {
            'match': {
                'match_id': '9990001', 'match_id2': '',
                'league': '测试联赛', 'home': '主队', 'away': '客队',
                'match_time': '2026-09-20 13:00:00',
            },
            'details': {},
            # 故意不含 capturedAt
        }
        raw = {
            'reportType': 'XDH_JCZQ_BATCH', 'version': '2.0.0',
            'exportedAt': None, 'total': 1, 'processed': 1,
            'matches': [entry],
        }
        results = self._run_normalize(raw, snap_captured_at=None)
        assert len(results) == 1
        assert results[0].action == 'pending_review'
        assert 'collected_at missing' in results[0].pending_reason

    def test_both_missing_no_snapshot_write(self):
        # 双重缺失时不应写入任何 match_snapshot
        entry = {
            'match': {
                'match_id': '9990001', 'match_id2': '',
                'league': '测试联赛', 'home': '主队', 'away': '客队',
                'match_time': '2026-09-20 13:00:00',
            },
            'details': {},
        }
        raw = {
            'reportType': 'XDH_JCZQ_BATCH', 'version': '2.0.0',
            'exportedAt': None, 'total': 1, 'processed': 1,
            'matches': [entry],
        }
        results = self._run_normalize(raw, snap_captured_at=None)
        # resolve_match 被调用前即返回（保护逻辑先于身份解析）
        assert results[0].action == 'pending_review'
        assert results[0].match_snapshot_id is None
        assert results[0].match_id is None

    def test_captured_at_present_passes_guard(self):
        # 快照 captured_at 存在 → 保护逻辑不应拦截
        entry = {
            'match': {
                'match_id': '9990001', 'match_id2': '',
                'league': '测试联赛', 'home': '主队', 'away': '客队',
                'match_time': '2026-09-20 13:00:00',
            },
            'details': {},
            'capturedAt': '2026-09-20T03:28:02.374Z',
        }
        raw = {
            'reportType': 'XDH_JCZQ_BATCH', 'version': '2.0.0',
            'exportedAt': None, 'total': 1, 'processed': 1,
            'matches': [entry],
        }
        snap_time = datetime(2026, 9, 20, 3, 28, 2, tzinfo=timezone.utc)
        results = self._run_normalize(raw, snap_captured_at=snap_time)
        assert results[0].action == 'pending_review'
        assert results[0].pending_reason == 'test-only'
        assert 'collected_at missing' not in results[0].pending_reason
