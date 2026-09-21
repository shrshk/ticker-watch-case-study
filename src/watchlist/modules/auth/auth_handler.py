"""Registration, login and token issue.

Raises domain errors, not HTTPException. Which status code a failure maps to is
a transport concern and belongs in the router; a handler that knows about HTTP
cannot be called from a Celery task, a CLI or a test without dragging FastAPI
along with it.
"""

import asyncpg

from watchlist.modules.auth import auth_controller
from watchlist.modules.auth.auth_schema import Session, User
from watchlist.shared.logging import get_logger
from watchlist.shared.security import hash_password, issue_token, verify_password

logger = get_logger(__name__)


class UsernameTakenError(Exception):
    """Registration collided with an existing username."""


class InvalidCredentialsError(Exception):
    """Unknown username or wrong password.

    Deliberately one error for both cases. Distinguishing them would let a
    caller enumerate which usernames exist.
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
    token, ttl = issue_token(user.id, user.username)
    logger.info("registered user %s", user.id)
    return Session(access_token=token, expires_in=ttl, user=user)


async def login(conn: asyncpg.Connection, username: str, password: str) -> Session:
    row = await auth_controller.get_by_username(conn, username)
    if row is None or not verify_password(password, row["password_hash"]):
        raise InvalidCredentialsError

    user = _to_user(row)
    token, ttl = issue_token(user.id, user.username)
    return Session(access_token=token, expires_in=ttl, user=user)


async def get_user(conn: asyncpg.Connection, user_id: int) -> User | None:
    row = await auth_controller.get_by_id(conn, user_id)
    return _to_user(row) if row else None
