"""Request and response shapes for the securities domain."""

from pydantic import BaseModel


class Security(BaseModel):
    id: int
    ticker: str
    name: str
    exchange: str | None = None
