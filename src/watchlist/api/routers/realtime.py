"""Realtime connection tokens. Transport only."""

from fastapi import APIRouter, Depends

from watchlist.api.deps import current_user
from watchlist.modules.auth.auth_schema import Principal
from watchlist.modules.realtime import realtime_handler
from watchlist.modules.realtime.realtime_schema import ConnectionToken

router = APIRouter(prefix="/realtime", tags=["realtime"])


@router.post("/token", response_model=ConnectionToken)
async def token(user: Principal = Depends(current_user)) -> ConnectionToken:
    return realtime_handler.connection_token(user.id)
