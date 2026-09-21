"""Raw queries for watchlist membership."""

import asyncpg


async def members(conn: asyncpg.Connection, watchlist_id: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT s.id, s.ticker, s.name, s.exchange "
        "FROM watchlist_items wi "
        "JOIN securities s ON s.id = wi.security_id "
        "WHERE wi.watchlist_id = $1 "
        "ORDER BY s.ticker",
        watchlist_id,
    )


async def add(conn: asyncpg.Connection, watchlist_id: int, security_id: int) -> bool:
    """Returns True if the row was inserted, False if it was already there."""
    result = await conn.execute(
        "INSERT INTO watchlist_items (watchlist_id, security_id) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING",
        watchlist_id,
        security_id,
    )
    return result.endswith(" 1")


async def remove(conn: asyncpg.Connection, watchlist_id: int, security_id: int) -> bool:
    result = await conn.execute(
        "DELETE FROM watchlist_items WHERE watchlist_id = $1 AND security_id = $2",
        watchlist_id,
        security_id,
    )
    return result.endswith(" 1")


async def watched_security_ids(conn: asyncpg.Connection) -> list[int]:
    """The union of every watchlisted security. A stock nobody watches costs nothing."""
    rows = await conn.fetch("SELECT DISTINCT security_id FROM watchlist_items")
    return [r["security_id"] for r in rows]
