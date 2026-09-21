"""Stock search. Transport only."""

import asyncpg
from fastapi import APIRouter, Depends, Query

from watchlist.api.deps import connection, current_user
from watchlist.modules.auth.auth_schema import User
from watchlist.modules.securities import securities_handler
from watchlist.modules.securities.securities_schema import Security

router = APIRouter(prefix="/securities", tags=["securities"])


@router.get("/search", response_model=list[Security])
async def search(
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(connection),
    _: User = Depends(current_user),
) -> list[Security]:
    return await securities_handler.search(conn, q, limit)
