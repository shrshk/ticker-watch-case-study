"""The snapshot read path is Redis-first with a Postgres fallback.

Definition of done #10: it hits Redis, falls through on a miss or an outage,
and never returns null for a price that exists.
"""

import datetime as dt

from watchlist.modules.prices import prices_controller, prices_handler
from watchlist.shared import cache
from watchlist.shared.settings import get_settings

EFFECTIVE_AT = dt.datetime(2026, 9, 21, 12, 0, 0, tzinfo=dt.UTC)


async def _security(conn, ticker: str) -> int:
    return await conn.fetchval(
        "INSERT INTO securities (ticker, name) VALUES ($1, $2) RETURNING id", ticker, ticker
    )


async def test_empty_watchlist_costs_nothing(conn):
    snapshot = await prices_handler.read_snapshot(conn, [])
    assert snapshot.prices == {}
    assert snapshot.cache_hits == 0


async def test_cache_hit_does_not_touch_postgres(conn, redis_client):
    sid = await _security(conn, "HIT")
    payload = {
        "security_id": sid,
        "price": 42.0,
        "effective_at": EFFECTIVE_AT.isoformat(),
        "source": "api",
    }
    await cache.set_price_if_newer("HIT", payload, EFFECTIVE_AT.isoformat(), 60)

    snapshot = await prices_handler.read_snapshot(conn, [(sid, "HIT")])

    assert snapshot.cache_hits == 1
    assert snapshot.cache_misses == 0
    assert snapshot.prices[sid].price == 42.0


async def test_cache_miss_falls_through_to_postgres(conn):
    sid = await _security(conn, "MISS")
    await prices_controller.upsert_many(conn, [(sid, 7.5, EFFECTIVE_AT, "api")])

    snapshot = await prices_handler.read_snapshot(conn, [(sid, "MISS")])

    assert snapshot.cache_hits == 0
    assert snapshot.cache_misses == 1
    assert snapshot.prices[sid].price == 7.5, "fell through but returned nothing"


async def test_partial_hit_fetches_only_the_missing_rows(conn, redis_client):
    hot = await _security(conn, "HOT")
    cold = await _security(conn, "COLD")
    payload = {
        "security_id": hot,
        "price": 1.0,
        "effective_at": EFFECTIVE_AT.isoformat(),
        "source": "api",
    }
    await cache.set_price_if_newer("HOT", payload, EFFECTIVE_AT.isoformat(), 60)
    await prices_controller.upsert_many(conn, [(cold, 2.0, EFFECTIVE_AT, "api")])

    snapshot = await prices_handler.read_snapshot(conn, [(hot, "HOT"), (cold, "COLD")])

    assert snapshot.cache_hits == 1
    assert snapshot.cache_misses == 1
    assert snapshot.prices[hot].price == 1.0
    assert snapshot.prices[cold].price == 2.0


async def test_postgres_read_path_bypasses_the_cache(conn, redis_client, monkeypatch):
    """LATEST_PRICE_SOURCE=postgres exists so the caching decision is measurable."""
    sid = await _security(conn, "BYPASS")
    stale = {
        "security_id": sid,
        "price": 999.0,
        "effective_at": EFFECTIVE_AT.isoformat(),
        "source": "api",
    }
    await cache.set_price_if_newer("BYPASS", stale, EFFECTIVE_AT.isoformat(), 60)
    await prices_controller.upsert_many(conn, [(sid, 3.0, EFFECTIVE_AT, "api")])

    settings = get_settings()
    monkeypatch.setattr(settings, "latest_price_source", "postgres")

    snapshot = await prices_handler.read_snapshot(conn, [(sid, "BYPASS")])

    assert snapshot.read_path == "postgres"
    assert snapshot.cache_hits == 0
    assert snapshot.prices[sid].price == 3.0, "the cache was consulted despite the toggle"


async def test_a_security_with_no_price_yields_no_entry(conn):
    """ATVI is in the vendor catalog and has no price. The client shows a dash."""
    sid = await _security(conn, "ATVI")
    snapshot = await prices_handler.read_snapshot(conn, [(sid, "ATVI")])
    assert sid not in snapshot.prices
