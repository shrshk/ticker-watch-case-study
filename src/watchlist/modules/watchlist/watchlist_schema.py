"""Request and response shapes for the watchlist domain."""

from pydantic import BaseModel

from watchlist.modules.securities.securities_schema import Security


class WatchlistItem(Security):
    price: float | None = None
    effective_at: str | None = None
    # 'api' or 'simulated'. Surfaced so nobody watching a demo mistakes
    # generated numbers for live market data.
    source: str | None = None


class Watchlist(BaseModel):
    items: list[WatchlistItem]
    # Which path served the prices, and how well the cache did. Cheap to
    # return, and it makes the LATEST_PRICE_SOURCE comparison observable from
    # the client instead of only from the metrics.
    read_path: str
    cache_hits: int
    cache_misses: int
    as_of: str


class AddItemRequest(BaseModel):
    security_id: int
