# 采集数据接收服务（连接池状态修复版）
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..db import get_pool

logger = logging.getLogger("football-prediction-api")


class IngestError(Exception):
    pass


class IngestResult:
    def __init__(self, status: str, run_id: str | None, snapshot_id: str | None, sha256: str, source: str, raw_file_path: str | None = None, size_bytes: int | None = None):
        self.status = status
        self.run_id = run_id
        self.snapshot_id = snapshot_id
        self.sha256 = sha256
        self.source = source
        self.raw_file_path = raw_file_path
        self.size_bytes = size_bytes

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "run_id": self.run_id,
            "snapshot_id": self.snapshot_id,
            "source": self.source,
            "sha256": self.sha256,
            "raw_file_path": self.raw_file_path,
            "size_bytes": self.size_bytes,
        }


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _lock_key(source: str, digest: str) -> int:
    raw = hashlib.sha256(f"{source}:{digest}".encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


def _relative_raw_path(source: str) -> str:
    now = datetime.now(timezone.utc)
    fname = f"{now:%Y%m%d_%H%M%S_%f}_{source}_{uuid.uuid4().hex[:8]}.json"
    return f"{now:%Y/%m/%d}/{fname}"


def _mark_run_failed(conn, run_id: str) -> None:
    # 在数据库连接可用且 UPDATE 成功的情况下，
    # 失败 run 会通过独立事务持久化为 failed；
    # 若数据库连接本身故障，失败标记可能不成功（如实描述，不做无条件保证）
    try:
        with conn.transaction():
            conn.execute(
                """
                UPDATE collection_runs
                SET status = 'failed', finished_at = now(), error_message = 'ingest failed'
                WHERE id = %s
                """,
                (run_id,),
            )
    except Exception:
        logger.exception("failed to mark run %s as failed", run_id)


def process_ingest(
    raw_bytes: bytes,
    parsed_data: dict | list,
    source: str,
    content_type: str,
) -> IngestResult:
    digest = _sha256_bytes(raw_bytes)
    pool = get_pool()
    lock_val = _lock_key(source, digest)
    run_id: str | None = None
    abs_path: Path | None = None
    lock_acquired = False

    with pool.connection() as conn:
        original_autocommit = conn.autocommit
        conn.autocommit = True
        try:
            # 1) 获取 session-level advisory lock（autocommit 下立即生效）
            conn.execute("SELECT pg_advisory_lock(%s)", (lock_val,))
            lock_acquired = True

            # 2) 永久按 (source, sha256) 去重，防止隔天重传重复创建快照
            cur = conn.execute(
                """
                SELECT id
                FROM collection_snapshots
                WHERE source = %s
                  AND entity_type = 'upload'
                  AND external_id = %s
                ORDER BY captured_at DESC
                LIMIT 1
                """,
                (source, digest),
            )
            row = cur.fetchone()
            if row:
                logger.info(
                    "result=duplicate source=%s size=%d sha12=%s run=%s snapshot=%s",
                    source, len(raw_bytes), digest[:12], None, row[0],
                )
                return IngestResult("duplicate", None, row[0], digest, source)

            # 3) 显式事务：创建 running run，正常退出即真实 COMMIT
            run_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            with conn.transaction():
                conn.execute(
                    """
                    INSERT INTO collection_runs
                    (id, source, run_type, started_at, status, items_collected)
                    VALUES (%s, %s, 'api_upload', %s, 'running', 0)
                    """,
                    (run_id, source, now),
                )

            # 4) 写 raw 文件（running 已提交之后）
            rel_path = _relative_raw_path(source)
            abs_path = Path(settings.RAW_DATA_DIR) / rel_path
            try:
                abs_path.parent.mkdir(parents=True, exist_ok=True)
                abs_path.write_bytes(raw_bytes)
            except Exception:
                if abs_path.exists():
                    try:
                        abs_path.unlink()
                    except Exception:
                        logger.exception("failed to clean partial file")
                _mark_run_failed(conn, run_id)
                logger.error(
                    "result=failed source=%s size=%d sha12=%s run=%s",
                    source, len(raw_bytes), digest[:12], run_id,
                )
                raise IngestError("ingest failed")

            # 5) 显式事务：UPDATE success 与 INSERT snapshot 同一事务提交
            snapshot_id = str(uuid.uuid4())
            wrapped = {
                "_ingest": {
                    "sha256": digest,
                    "raw_file_path": rel_path,
                    "received_at": now.isoformat(),
                    "source": source,
                    "content_type": content_type,
                    "size_bytes": len(raw_bytes),
                },
                "data": parsed_data,
            }
            try:
                with conn.transaction():
                    conn.execute(
                        """
                        UPDATE collection_runs
                        SET status = 'success', finished_at = %s, items_collected = 1
                        WHERE id = %s
                        """,
                        (now, run_id),
                    )
                    conn.execute(
                        """
                        INSERT INTO collection_snapshots
                        (id, run_id, source, entity_type, external_id, raw_payload, captured_at)
                        VALUES (%s, %s, %s, 'upload', %s, %s::jsonb, %s)
                        """,
                        (snapshot_id, run_id, source, digest, json.dumps(wrapped, ensure_ascii=False, default=str), now),
                    )
            except Exception:
                if abs_path.exists():
                    try:
                        abs_path.unlink()
                    except Exception:
                        logger.exception("failed to clean file after db failure")
                _mark_run_failed(conn, run_id)
                logger.error(
                    "result=failed source=%s size=%d sha12=%s run=%s",
                    source, len(raw_bytes), digest[:12], run_id,
                )
                raise IngestError("ingest failed")

            logger.info(
                "result=success source=%s size=%d sha12=%s run=%s snapshot=%s",
                source, len(raw_bytes), digest[:12], run_id, snapshot_id,
            )
            return IngestResult("success", run_id, snapshot_id, digest, source, rel_path, len(raw_bytes))
        finally:
            # 顺序：先安全释放 advisory lock，再恢复 conn.autocommit
            if lock_acquired:
                try:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (lock_val,))
                except Exception:
                    logger.exception("failed to release advisory lock")
            try:
                conn.autocommit = original_autocommit
            except Exception:
                logger.exception("failed to restore autocommit")
