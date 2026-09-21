"""Shapes for the realtime domain."""

from pydantic import BaseModel


class ConnectionToken(BaseModel):
    token: str
    expires_in: int
    # Clients subscribe to f"{channel_prefix}{ticker}" - one channel per ticker,
    # never one per user.
    channel_prefix: str
