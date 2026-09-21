"""FastAPI dependencies: the connection pool and the current user."""

from collections.abc import AsyncIterator

import asyncpg
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from watchlist.modules.auth.auth_schema import Principal
from watchlist.shared import db
from watchlist.shared.security import decode_token

bearer = HTTPBearer(auto_error=False)


async def connection() -> AsyncIterator[asyncpg.Connection]:
    async with db.pool().acquire() as conn:
        yield conn


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> Principal:
    """Verify the token and read the caller out of it. No database round trip.

    The earlier version also looked the user up to catch "user no longer
    exists", which made every request pay for a stateful check while still
    trusting a stateless token for everything else - the cost of one model with
    the guarantees of the other. See Principal for the trade this makes instead.
    """
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc
    return Principal(id=int(payload["sub"]), username=payload["username"])
