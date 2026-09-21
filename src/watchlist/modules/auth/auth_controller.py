"""Raw queries for users and their default watchlist."""

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
    """Every user has exactly one watchlist. Create it lazily for seeded rows."""
    row = await conn.fetchrow(
        "INSERT INTO watchlists (user_id, name) VALUES ($1, 'default') "
        "ON CONFLICT (user_id, name) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING id",
        user_id,
    )
    return row["id"]
