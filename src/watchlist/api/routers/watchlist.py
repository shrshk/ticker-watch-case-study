"""Watchlist membership and the price snapshot. Transport only."""

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Response, status

from watchlist.api.deps import connection, current_user
from watchlist.modules.auth.auth_schema import Principal
from watchlist.modules.watchlist import watchlist_handler
from watchlist.modules.watchlist.watchlist_schema import AddItemRequest, Watchlist

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=Watchlist)
async def get_watchlist(
    conn: asyncpg.Connection = Depends(connection),
    user: Principal = Depends(current_user),
) -> Watchlist:
    return await watchlist_handler.get_watchlist(conn, user.id)


@router.post("/items", status_code=status.HTTP_204_NO_CONTENT)
async def add_item(
    body: AddItemRequest,
    conn: asyncpg.Connection = Depends(connection),
    user: Principal = Depends(current_user),
) -> Response:
    try:
        await watchlist_handler.add_item(conn, user.id, body.security_id)
    except watchlist_handler.SecurityNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such security") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/items/{security_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_item(
    security_id: int,
    conn: asyncpg.Connection = Depends(connection),
    user: Principal = Depends(current_user),
) -> Response:
    try:
        await watchlist_handler.remove_item(conn, user.id, security_id)
    except watchlist_handler.NotOnWatchlistError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not on your watchlist") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
