"""asyncpg connection pool.

Raw SQL over an ORM on purpose: the snapshot read path and the COPY-based
seed path are both hot, and both are clearer as SQL than as ORM calls.
"""

import asyncpg

from watchlist.shared.settings import get_settings

_pool: asyncpg.Pool | None = None


async def connect(min_size: int = 2, max_size: int = 16) -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=get_settings().postgres_dsn,
            min_size=min_size,
            max_size=max_size,
            command_timeout=30,
        )
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
