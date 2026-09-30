"""Async Postgres connection pool (asyncpg)."""

import json
import logging

import asyncpg

logger = logging.getLogger(__name__)

# Every query gets this timeout (seconds) unless the caller passes its own.
QUERY_TIMEOUT_S = 5.0

_pool: asyncpg.Pool | None = None


class DatabaseUnavailableError(RuntimeError):
    """Raised when a query is attempted without a connection pool."""


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Store and return JSONB columns as Python dicts/lists.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def open_pool(dsn: str) -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=10,
        command_timeout=QUERY_TIMEOUT_S,
        # Supabase's pooler (PgBouncer) does not support prepared-statement caching.
        statement_cache_size=0,
        init=_init_connection,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise DatabaseUnavailableError("database pool is not initialised")
    return _pool


def set_pool(pool: asyncpg.Pool | None) -> None:
    """Install a pool directly (used by tests)."""
    global _pool
    _pool = pool


async def ping() -> bool:
    try:
        return bool(await get_pool().fetchval("SELECT 1"))
    except DatabaseUnavailableError:
        return False
    except Exception:
        logger.exception("database ping failed")
        return False
