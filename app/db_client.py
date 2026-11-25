"""Conexão/pool PostgreSQL Aiven para o dashboard do Cruzeiro."""
import os
from contextlib import contextmanager
import psycopg2
from psycopg2 import pool as psycopg2_pool
from psycopg2.extras import RealDictCursor

DB_URL = os.environ.get("AIVEN_DB_URL")
_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        if not DB_URL:
            raise RuntimeError("AIVEN_DB_URL não configurada")
        _pool = psycopg2_pool.ThreadedConnectionPool(
            minconn=1, maxconn=5, dsn=DB_URL, sslmode="require",
            cursor_factory=RealDictCursor, connect_timeout=10,
        )
    return _pool



@contextmanager
def get_connection():
    conn = _get_pool().getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        _get_pool().putconn(conn)
