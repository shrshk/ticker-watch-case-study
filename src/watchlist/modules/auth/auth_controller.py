"""Raw queries for users, their default watchlist, and refresh tokens."""

import datetime as dt

import asyncpg


async def get_by_username(conn: asyncpg.Connection, username: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, username, email, first_name, last_name, password_hash "
        "FROM users WHERE username = $1",
        username,
    )


async def get_by_id(conn: asyncpg.Connection, user_id: int) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, username, email, first_name, last_name FROM users WHERE id = $1",
        user_id,
    )


async def create(
    conn: asyncpg.Connection,
    username: str,
    password_hash: str,
    email: str | None,
    first_name: str,
    last_name: str,
) -> asyncpg.Record:
    """Create the user and their default watchlist in one transaction."""
    async with conn.transaction():
        row = await conn.fetchrow(
            "INSERT INTO users (username, password_hash, email, first_name, last_name) "
            "VALUES ($1, $2, $3, $4, $5) "
            "RETURNING id, username, email, first_name, last_name",
            username,
            password_hash,
            email,
            first_name,
            last_name,
        )
        await conn.execute(
            "INSERT INTO watchlists (user_id, name) VALUES ($1, 'default') "
            "ON CONFLICT (user_id, name) DO NOTHING",
            row["id"],
        )
    return row


async def default_watchlist_id(conn: asyncpg.Connection, user_id: int) -> int:
    """Every user has exactly one watchlist.

    Read first, create only on a miss. The previous version was a single
    INSERT ... ON CONFLICT DO UPDATE, which is elegant and wrong on the read
    path: Postgres performs the UPDATE even when nothing changes, so every
    GET /watchlist wrote a tuple version, a WAL record and took a row lock.
    pg_stat_user_tables showed 8.8M updates on `watchlists` from polling alone.
    """
    watchlist_id = await conn.fetchval(
        "SELECT id FROM watchlists WHERE user_id = $1 AND name = 'default'", user_id
    )
    if watchlist_id is not None:
        return watchlist_id
    # Registration and seeding both create the row, so this path is for rows
    # that predate them. ON CONFLICT covers a concurrent first request.
    return await conn.fetchval(
        "INSERT INTO watchlists (user_id, name) VALUES ($1, 'default') "
        "ON CONFLICT (user_id, name) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING id",
        user_id,
    )


# -- refresh tokens -----------------------------------------------------------


async def store_refresh_token(
    conn: asyncpg.Connection, user_id: int, token_hash: str, expires_at: dt.datetime
) -> None:
    await conn.execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) VALUES ($1, $2, $3)",
        user_id,
        token_hash,
        expires_at,
    )


async def get_refresh_token(conn: asyncpg.Connection, token_hash: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        "SELECT id, user_id, expires_at, revoked_at FROM refresh_tokens WHERE token_hash = $1",
        token_hash,
    )


async def revoke_refresh_token(conn: asyncpg.Connection, token_id: int) -> None:
    await conn.execute(
        "UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL",
        token_id,
    )


async def revoke_all_refresh_tokens(conn: asyncpg.Connection, user_id: int) -> int:
    """Every live refresh token for a user. Returns how many were revoked."""
    result = await conn.execute(
        "UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
        user_id,
    )
    return int(result.rsplit(" ", 1)[-1])
