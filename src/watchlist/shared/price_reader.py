"""The snapshot read path: Redis first, latest_prices behind it.

Postgres is authoritative; Redis is a read-through cache in front of it. A
miss, an expired key or a Redis outage falls through and still returns a
correct answer - never a null. Drift self-heals on the next tick.

LATEST_PRICE_SOURCE=postgres bypasses the cache entirely. That toggle exists so
the caching decision can be measured rather than asserted.
"""

import dataclasses
import datetime as dt
import logging

import asyncpg
import redis.exceptions

from watchlist.shared import cache
from watchlist.shared.repo import prices as price_repo
from watchlist.shared.settings import get_settings

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PriceSnapshot:
    prices: dict[int, "Price"]
    cache_hits: int
    cache_misses: int
    read_path: str


@dataclasses.dataclass(frozen=True)
class Price:
    security_id: int
    price: float
    effective_at: str
    source: str


async def read(
    conn: asyncpg.Connection,
    securities: list[tuple[int, str]],
) -> PriceSnapshot:
    """Fetch current prices for (security_id, ticker) pairs."""
    if not securities:
        return PriceSnapshot({}, 0, 0, get_settings().latest_price_source)

    settings = get_settings()
    by_ticker = {ticker: sid for sid, ticker in securities}
    found: dict[int, Price] = {}
    hits = 0
    read_path = settings.latest_price_source

    if settings.latest_price_source == "redis":
        try:
            cached = await cache.get_prices(list(by_ticker))
        except (redis.exceptions.RedisError, OSError) as exc:
            # The whole point of the fallback. Stay correct, get slower.
            logger.warning("price cache unavailable, falling through to postgres: %s", exc)
            cached = {}
            read_path = "postgres (redis unavailable)"
        for ticker, payload in cached.items():
            sid = by_ticker[ticker]
            found[sid] = Price(
                security_id=sid,
                price=payload["price"],
                effective_at=payload["effective_at"],
                source=payload.get("source", "unknown"),
            )
        hits = len(found)

    missing = [sid for sid, _ in securities if sid not in found]
    if missing:
        for row in await price_repo.get_many(conn, missing):
            found[row["security_id"]] = Price(
                security_id=row["security_id"],
                price=float(row["price"]),
                effective_at=_iso(row["effective_at"]),
                source=row["source"],
            )

    return PriceSnapshot(
        prices=found,
        cache_hits=hits,
        cache_misses=len(missing),
        read_path=read_path,
    )


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
