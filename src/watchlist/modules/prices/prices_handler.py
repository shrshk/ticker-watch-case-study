"""The snapshot read path: Redis first, latest_prices behind it.

Postgres is authoritative; Redis is a read-through cache in front of it. A
miss, an expired key or a Redis outage falls through and still returns a
correct answer - never a null. Drift self-heals on the next tick.

LATEST_PRICE_SOURCE=postgres bypasses the cache entirely. That toggle exists so
the caching decision can be measured rather than asserted.
"""

import dataclasses
import datetime as dt

import asyncpg
import redis.exceptions

from watchlist.modules.prices import prices_controller
from watchlist.shared import cache
from watchlist.shared.logging import get_logger
from watchlist.shared.settings import get_settings

logger = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class Price:
    security_id: int
    price: float
    effective_at: str
    source: str


@dataclasses.dataclass(frozen=True)
class PriceSnapshot:
    """What a read returned, plus how it was served.

    The provenance is part of the result on purpose. It is what the status bar
    in the client shows and what makes the LATEST_PRICE_SOURCE comparison
    observable from outside, instead of only in the metrics.
    """

    prices: dict[int, Price]
    cache_hits: int
    cache_misses: int
    read_path: str


class SnapshotReader:
    """Read current prices for one watchlist.

    One instance per read. It carries the per-read counters between steps,
    which is what keeps `read` a short description of the path rather than a
    function threading four accumulators through itself.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._settings = get_settings()
        self._found: dict[int, Price] = {}
        self._cache_hits = 0
        self._read_path = self._settings.latest_price_source

    async def read(self, securities: list[tuple[int, str]]) -> PriceSnapshot:
        """Fetch prices for (security_id, ticker) pairs."""
        if not securities:
            return PriceSnapshot({}, 0, 0, self._read_path)

        if self._settings.latest_price_source == "redis":
            await self._from_cache(securities)

        missing = [sid for sid, _ in securities if sid not in self._found]
        if missing:
            await self._from_database(missing)

        return PriceSnapshot(
            prices=self._found,
            cache_hits=self._cache_hits,
            cache_misses=len(missing),
            read_path=self._read_path,
        )

    async def _from_cache(self, securities: list[tuple[int, str]]) -> None:
        by_ticker = {ticker: sid for sid, ticker in securities}
        try:
            cached = await cache.get_prices(list(by_ticker))
        except (redis.exceptions.RedisError, OSError) as exc:
            # The whole point of the fallback: stay correct, get slower.
            logger.warning("price cache unavailable, falling through to postgres: %s", exc)
            self._read_path = "postgres (redis unavailable)"
            return

        for ticker, payload in cached.items():
            sid = by_ticker[ticker]
            self._found[sid] = Price(
                security_id=sid,
                price=payload["price"],
                effective_at=payload["effective_at"],
                source=payload.get("source", "unknown"),
            )
        self._cache_hits = len(self._found)

    async def _from_database(self, security_ids: list[int]) -> None:
        for row in await prices_controller.get_many(self._conn, security_ids):
            self._found[row["security_id"]] = Price(
                security_id=row["security_id"],
                price=float(row["price"]),
                effective_at=to_iso(row["effective_at"]),
                source=row["source"],
            )


async def read_snapshot(
    conn: asyncpg.Connection, securities: list[tuple[int, str]]
) -> PriceSnapshot:
    """Convenience wrapper for the common single-read case."""
    return await SnapshotReader(conn).read(securities)


def to_iso(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
