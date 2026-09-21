"""Registration, login and token issue.

Raises domain errors, not HTTPException. Which status code a failure maps to is
a transport concern and belongs in the router; a handler that knows about HTTP
cannot be called from a Celery task, a CLI or a test without dragging FastAPI
along with it.
"""

import datetime as dt

import asyncpg

from watchlist.modules.auth import auth_controller
from watchlist.modules.auth.auth_schema import Session, User
from watchlist.shared.logging import get_logger
from watchlist.shared.security import (
    hash_password,
    hash_refresh_token,
    issue_token,
    new_refresh_token,
    verify_password,
)
from watchlist.shared.settings import get_settings

logger = get_logger(__name__)


class UsernameTakenError(Exception):
    """Registration collided with an existing username."""


class InvalidCredentialsError(Exception):
    """Unknown username or wrong password.

    Deliberately one error for both cases. Distinguishing them would let a
    caller enumerate which usernames exist.
    """


class InvalidRefreshTokenError(Exception):
    """Unknown, expired, or already-used refresh token.

    One error for all three, for the same reason as InvalidCredentialsError.
    The already-used case additionally revokes every refresh token the user
    holds: a rotated token being presented again means either the client
    replayed it or someone else has it, and the safe response to both is to
    end all sessions and make them log in again.
    """


def _to_user(row: asyncpg.Record) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        email=row["email"],
        first_name=row["first_name"],
        last_name=row["last_name"],
    )


async def register(
    conn: asyncpg.Connection,
    username: str,
    password: str,
    email: str | None = None,
    first_name: str = "",
    last_name: str = "",
) -> Session:
    try:
        row = await auth_controller.create(
            conn,
            username=username,
            password_hash=hash_password(password),
            email=email,
            first_name=first_name,
            last_name=last_name,
        )
    except asyncpg.UniqueViolationError as exc:
        raise UsernameTakenError(username) from exc

    user = _to_user(row)
    logger.info("registered user %s", user.id)
    return await _issue_session(conn, user)


async def login(conn: asyncpg.Connection, username: str, password: str) -> Session:
    row = await auth_controller.get_by_username(conn, username)
    if row is None or not verify_password(password, row["password_hash"]):
        raise InvalidCredentialsError

    return await _issue_session(conn, _to_user(row))


async def get_user(conn: asyncpg.Connection, user_id: int) -> User | None:
    row = await auth_controller.get_by_id(conn, user_id)
    return _to_user(row) if row else None


async def _issue_session(conn: asyncpg.Connection, user: User) -> Session:
    """A short-lived access token and a fresh, stored refresh token."""
    access, ttl = issue_token(user.id, user.username)
    refresh = new_refresh_token()
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(
        seconds=get_settings().refresh_token_ttl_seconds
    )
    await auth_controller.store_refresh_token(
        conn, user.id, hash_refresh_token(refresh), expires_at
    )
    return Session(access_token=access, expires_in=ttl, refresh_token=refresh, user=user)


async def refresh(conn: asyncpg.Connection, refresh_token: str) -> Session:
    """Exchange a refresh token for a new pair. The presented token is retired.

    This is the only step in the auth flow that reads the database once a user
    is logged in, and it runs once per access-token lifetime rather than once
    per request. It is also where revocation lives: a deleted user has no row
    to find (their tokens cascaded away), so the next refresh fails and the
    session ends within one access-token TTL.
    """
    row = await auth_controller.get_refresh_token(conn, hash_refresh_token(refresh_token))
    if row is None:
        raise InvalidRefreshTokenError

    if row["revoked_at"] is not None:
        # Reuse of a rotated token. Treat as compromise: end every session.
        revoked = await auth_controller.revoke_all_refresh_tokens(conn, row["user_id"])
        logger.warning(
            "refresh token reuse detected for user %s; revoked %d tokens", row["user_id"], revoked
        )
        raise InvalidRefreshTokenError

    if row["expires_at"] <= dt.datetime.now(dt.UTC):
        raise InvalidRefreshTokenError

    user = await get_user(conn, row["user_id"])
    if user is None:
        raise InvalidRefreshTokenError

    async with conn.transaction():
        await auth_controller.revoke_refresh_token(conn, row["id"])
        return await _issue_session(conn, user)


async def logout(conn: asyncpg.Connection, refresh_token: str) -> None:
    """Retire one refresh token. Idempotent; an unknown token is not an error."""
    row = await auth_controller.get_refresh_token(conn, hash_refresh_token(refresh_token))
    if row is not None:
        await auth_controller.revoke_refresh_token(conn, row["id"])
