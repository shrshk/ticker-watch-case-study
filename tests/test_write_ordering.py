"""Both latest-price writes are guarded on effective_at.

Definition of done #11: an out-of-order replay must leave neither store stale.
Two price-service workers, or a retry after a partial failure, can write an
older price after a newer one, and neither store detects this on its own.
"""

import datetime as dt

from watchlist.modules.prices import prices_controller
from watchlist.shared import cache

NEWER = dt.datetime(2026, 9, 21, 12, 0, 0, tzinfo=dt.UTC)
OLDER = dt.datetime(2026, 9, 21, 11, 0, 0, tzinfo=dt.UTC)


async def _security(conn, ticker: str = "TEST") -> int:
    return await conn.fetchval(
        "INSERT INTO securities (ticker, name) VALUES ($1, $2) RETURNING id", ticker, "Test Corp"
    )


class TestPostgresGuard:
    async def test_older_write_is_rejected(self, conn):
        sid = await _security(conn)
        await prices_controller.upsert_many(conn, [(sid, 100.0, NEWER, "api")])
        await prices_controller.upsert_many(conn, [(sid, 50.0, OLDER, "api")])

        row = (await prices_controller.get_many(conn, [sid]))[0]
        assert row["price"] == 100.0, "an older replay overwrote a newer price"
        assert row["effective_at"] == NEWER

    async def test_newer_write_wins(self, conn):
        sid = await _security(conn)
        await prices_controller.upsert_many(conn, [(sid, 50.0, OLDER, "api")])
        await prices_controller.upsert_many(conn, [(sid, 100.0, NEWER, "api")])

        row = (await prices_controller.get_many(conn, [sid]))[0]
        assert row["price"] == 100.0
        assert row["effective_at"] == NEWER


class TestRedisGuard:
    async def test_older_write_is_rejected(self, redis_client):
        newer = {"security_id": 1, "price": 100.0, "effective_at": NEWER.isoformat()}
        older = {"security_id": 1, "price": 50.0, "effective_at": OLDER.isoformat()}

        assert await cache.set_price_if_newer("TEST", newer, NEWER.isoformat(), 60) is True
        assert await cache.set_price_if_newer("TEST", older, OLDER.isoformat(), 60) is False

        stored = await cache.get_prices(["TEST"])
        assert stored["TEST"]["price"] == 100.0, "an older replay overwrote the cache"

    async def test_equal_timestamp_refreshes_the_ttl(self, redis_client):
        """An unchanged price is rewritten every tick to keep a quiet ticker alive."""
        payload = {"security_id": 1, "price": 100.0, "effective_at": NEWER.isoformat()}
        await cache.set_price_if_newer("TEST", payload, NEWER.isoformat(), 5)
        assert await redis_client.ttl("price:TEST") <= 5

        assert await cache.set_price_if_newer("TEST", payload, NEWER.isoformat(), 300) is True
        assert await redis_client.ttl("price:TEST") > 5

    async def test_newer_write_wins(self, redis_client):
        older = {"security_id": 1, "price": 50.0, "effective_at": OLDER.isoformat()}
        newer = {"security_id": 1, "price": 100.0, "effective_at": NEWER.isoformat()}

        await cache.set_price_if_newer("TEST", older, OLDER.isoformat(), 60)
        assert await cache.set_price_if_newer("TEST", newer, NEWER.isoformat(), 60) is True

        stored = await cache.get_prices(["TEST"])
        assert stored["TEST"]["price"] == 100.0
