"""Watchlist membership and the price snapshot.

GET /watchlist returns membership and prices together. Two round trips on app
load is a worse story than one, and it keeps the subscribe-then-snapshot
ordering rule simple to implement in phase 3.
"""

import datetime as dt

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from watchlist.api.deps import connection, current_user
from watchlist.api.schemas import AddItemRequest, WatchlistItemOut, WatchlistOut
from watchlist.shared import price_reader
from watchlist.shared.repo import securities as security_repo
from watchlist.shared.repo import users as user_repo
from watchlist.shared.repo import watchlists as watchlist_repo

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=WatchlistOut)
async def get_watchlist(
    conn: asyncpg.Connection = Depends(connection),
    user: asyncpg.Record = Depends(current_user),
) -> WatchlistOut:
    watchlist_id = await user_repo.default_watchlist_id(conn, user["id"])
    members = await watchlist_repo.members(conn, watchlist_id)

    snapshot = await price_reader.read(conn, [(r["id"], r["ticker"]) for r in members])

    items = []
    for row in members:
        price = snapshot.prices.get(row["id"])
        items.append(
            WatchlistItemOut(
                id=row["id"],
                ticker=row["ticker"],
                name=row["name"],
                exchange=row["exchange"],
                price=price.price if price else None,
                effective_at=price.effective_at if price else None,
                source=price.source if price else None,
            )
        )

    return WatchlistOut(
        items=items,
        read_path=snapshot.read_path,
        cache_hits=snapshot.cache_hits,
        cache_misses=snapshot.cache_misses,
        as_of=dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
    )


@router.post("/items", status_code=status.HTTP_204_NO_CONTENT)
async def add_item(
    body: AddItemRequest,
    conn: asyncpg.Connection = Depends(connection),
    user: asyncpg.Record = Depends(current_user),
) -> Response:
    if await security_repo.get(conn, body.security_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such security")
    watchlist_id = await user_repo.default_watchlist_id(conn, user["id"])
    await watchlist_repo.add(conn, watchlist_id, body.security_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/items/{security_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_item(
    security_id: int,
    conn: asyncpg.Connection = Depends(connection),
    user: asyncpg.Record = Depends(current_user),
) -> Response:
    watchlist_id = await user_repo.default_watchlist_id(conn, user["id"])
    if not await watchlist_repo.remove(conn, watchlist_id, security_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not on your watchlist")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
