"""Stock search."""

import asyncpg
from fastapi import APIRouter, Depends, Query

from watchlist.api.deps import connection, current_user
from watchlist.api.schemas import SecurityOut
from watchlist.shared.repo import securities as security_repo

router = APIRouter(prefix="/securities", tags=["securities"])


@router.get("/search", response_model=list[SecurityOut])
async def search(
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=20, ge=1, le=100),
    conn: asyncpg.Connection = Depends(connection),
    _: asyncpg.Record = Depends(current_user),
) -> list[SecurityOut]:
    rows = await security_repo.search(conn, q.strip(), limit)
    return [
        SecurityOut(id=r["id"], ticker=r["ticker"], name=r["name"], exchange=r["exchange"])
        for r in rows
    ]
