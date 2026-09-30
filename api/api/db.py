"""数据库访问：DB 不可用时返回 None，路由降级为演示数据。"""
from . import config

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover
    psycopg = None


def get_conn():
    if not psycopg or not config.DATABASE_URL:
        return None
    try:
        return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)
    except Exception:
        return None
