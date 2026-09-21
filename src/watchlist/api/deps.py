"""FastAPI dependencies: the connection pool and the current user."""

from collections.abc import AsyncIterator

import asyncpg
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from watchlist.shared import db
from watchlist.shared.repo import users as user_repo
from watchlist.shared.security import decode_token

bearer = HTTPBearer(auto_error=False)


async def connection() -> AsyncIterator[asyncpg.Connection]:
    async with db.pool().acquire() as conn:
        yield conn


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    conn: asyncpg.Connection = Depends(connection),
) -> asyncpg.Record:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc

    user = await user_repo.get_by_id(conn, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user no longer exists")
    return user
