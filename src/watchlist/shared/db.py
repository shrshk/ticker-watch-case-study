"""asyncpg connection pool.

Raw SQL over an ORM on purpose: the snapshot read path and the COPY-based
seed path are both hot, and both are clearer as SQL than as ORM calls.
"""

import asyncpg

from watchlist.shared.logging import get_logger
from watchlist.shared.settings import get_settings

logger = get_logger(__name__)

_pool: asyncpg.Pool | None = None


async def connect(min_size: int | None = None, max_size: int | None = None) -> asyncpg.Pool:
    """Open this process's pool.

    The size is per process. See the note on `db_pool_max_size` in settings:
    every uvicorn worker opens its own pool, so the server-wide total is
    workers x max_size and has to fit under Postgres's max_connections.
    """
    global _pool
    if _pool is None:
        settings = get_settings()
        min_size = settings.db_pool_min_size if min_size is None else min_size
        max_size = settings.db_pool_max_size if max_size is None else max_size
        _pool = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
        )
        logger.info("database pool open (min=%d max=%d per process)", min_size, max_size)
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialised; call connect() first")
    return _pool
