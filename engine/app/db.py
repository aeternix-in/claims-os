from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(get_settings().database_url, min_size=1, max_size=10,
                               kwargs={"row_factory": dict_row, "prepare_threshold": None}, open=True)
    return _pool


@contextmanager
def tx():
    """One transaction; commits on success, rolls back on error."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn
