"""Raw queries for latest_prices - the durable snapshot source."""

import datetime as dt

import asyncpg

# Guarded on effective_at. Two price-service workers, or a retry after partial
# failure, can otherwise write an older price after a newer one.
_UPSERT_SQL = """
INSERT INTO latest_prices (security_id, price, effective_at, source)
SELECT * FROM unnest($1::bigint[], $2::double precision[], $3::timestamptz[], $4::text[])
ON CONFLICT (security_id) DO UPDATE
SET price        = excluded.price,
    effective_at = excluded.effective_at,
    source       = excluded.source,
    updated_at   = now()
WHERE excluded.effective_at > latest_prices.effective_at
"""


async def upsert_many(
    conn: asyncpg.Connection,
    rows: list[tuple[int, float, dt.datetime, str]],
) -> None:
    if not rows:
        return
    await conn.execute(
        _UPSERT_SQL,
        [r[0] for r in rows],
        [r[1] for r in rows],
        [r[2] for r in rows],
        [r[3] for r in rows],
    )


async def get_many(conn: asyncpg.Connection, security_ids: list[int]) -> list[asyncpg.Record]:
    if not security_ids:
        return []
    return await conn.fetch(
        "SELECT security_id, price, effective_at, source "
        "FROM latest_prices WHERE security_id = ANY($1::bigint[])",
        security_ids,
    )


async def all_with_tickers(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Everything needed to warm the cache on boot."""
    return await conn.fetch(
        "SELECT lp.security_id, s.ticker, lp.price, lp.effective_at, lp.source "
        "FROM latest_prices lp JOIN securities s ON s.id = lp.security_id"
    )


async def distinct_sources(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch("SELECT DISTINCT source FROM latest_prices")
    return [r["source"] for r in rows]


async def delete_by_source(conn: asyncpg.Connection, source: str) -> str:
    return await conn.execute("DELETE FROM latest_prices WHERE source = $1", source)
