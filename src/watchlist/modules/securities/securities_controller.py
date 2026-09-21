"""Raw queries for the security catalog and search."""

import asyncpg

# Rank: exact ticker, then ticker prefix, then name prefix, then fuzzy name.
# Postgres only. Elasticsearch would need a measured requirement to justify it.
_SEARCH_SQL = """
SELECT id, ticker, name, exchange
FROM securities
WHERE ticker ILIKE $1 || '%'
   OR name   ILIKE '%' || $1 || '%'
ORDER BY
    CASE
        WHEN upper(ticker) = upper($1)        THEN 0
        WHEN ticker ILIKE $1 || '%'           THEN 1
        WHEN name   ILIKE $1 || '%'           THEN 2
        ELSE 3
    END,
    length(ticker),
    ticker
LIMIT $2
"""


async def search(conn: asyncpg.Connection, query: str, limit: int = 20) -> list[asyncpg.Record]:
    return await conn.fetch(_SEARCH_SQL, query, limit)


async def all_tickers(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch("SELECT id, ticker FROM securities ORDER BY ticker")


async def get(conn: asyncpg.Connection, security_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, ticker, name, exchange FROM securities WHERE id = $1", security_id
    )


async def upsert_many(conn: asyncpg.Connection, rows: list[tuple[str, str]]) -> None:
    """Upsert (ticker, name) pairs from the vendor catalog."""
    if not rows:
        return
    await conn.executemany(
        "INSERT INTO securities (ticker, name) VALUES ($1, $2) "
        "ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name",
        rows,
    )
