# 数据库连接模块
from psycopg_pool import ConnectionPool

from .config import settings


pool: ConnectionPool | None = None


def init_db() -> None:
    global pool
    cfg = settings.effective_db_config()
    conninfo = (
        f"host={cfg['host']} port={cfg['port']} "
        f"dbname={cfg['dbname']} user={cfg['user']} "
        f"password={cfg['password']}"
    )
    pool = ConnectionPool(conninfo, min_size=1, max_size=5, open=False)
    pool.open()
    with pool.connection() as conn:
        conn.execute("SELECT 1")


def check_db() -> bool:
    if pool is None:
        return False
    try:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def get_pool() -> ConnectionPool:
    if pool is None:
        raise RuntimeError("database pool not initialized")
    return pool


def close_db() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None
