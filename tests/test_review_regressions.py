"""Regression tests for defects found in the pre-phase-3 review.

Each test names the finding it pins. If one of these starts failing, the
review's fix has been undone.
"""

import datetime as dt

from watchlist.modules.auth import auth_controller, auth_handler
from watchlist.shared import cache
from watchlist.shared.timeutil import to_iso


class TestC1ReadPathDoesNotWrite:
    """GET /watchlist resolved the watchlist with INSERT ... ON CONFLICT DO UPDATE,
    which Postgres executes as a real UPDATE even when nothing changes. 8.8M
    updates on `watchlists` came from polling alone."""

    async def test_resolving_an_existing_watchlist_performs_no_update(self, conn):
        user = (await auth_handler.register(conn, "c1_user", "pw")).user

        # pg_stat_xact_user_tables counts tuple operations in the *current*
        # transaction, which is exactly the scope the test fixture gives us.
        before = await conn.fetchval(
            "SELECT coalesce(sum(n_tup_upd), 0) FROM pg_stat_xact_user_tables "
            "WHERE relname = 'watchlists'"
        )
        for _ in range(5):
            await auth_controller.default_watchlist_id(conn, user.id)
        after = await conn.fetchval(
            "SELECT coalesce(sum(n_tup_upd), 0) FROM pg_stat_xact_user_tables "
            "WHERE relname = 'watchlists'"
        )

        assert after == before, "resolving a watchlist wrote to it"

    async def test_a_missing_watchlist_is_still_created(self, conn):
        uid = await conn.fetchval(
            "INSERT INTO users (username, password_hash) VALUES ('c1_seeded', 'x') RETURNING id"
        )
        first = await auth_controller.default_watchlist_id(conn, uid)
        second = await auth_controller.default_watchlist_id(conn, uid)
        assert first == second


class TestH1TimestampOrdering:
    """The Redis guard compares effective_at as strings. isoformat() drops the
    fractional part when microsecond == 0, and "...20Z" sorts after
    "...20.999999Z" because "Z" > ".", so a write at exactly .000000 would beat
    a newer one from the same second."""

    def test_zero_microseconds_still_has_six_fractional_digits(self):
        exact = dt.datetime(2026, 9, 21, 12, 0, 20, 0, tzinfo=dt.UTC)
        assert to_iso(exact) == "2026-09-21T12:00:20.000000Z"

    def test_lexical_order_equals_chronological_order_across_the_second_boundary(self):
        older = dt.datetime(2026, 9, 21, 12, 0, 20, 999_999, tzinfo=dt.UTC)
        newer = dt.datetime(2026, 9, 21, 12, 0, 21, 0, tzinfo=dt.UTC)
        assert to_iso(older) < to_iso(newer)

    def test_lexical_order_equals_chronological_order_within_a_second(self):
        exact = dt.datetime(2026, 9, 21, 12, 0, 20, 0, tzinfo=dt.UTC)
        later = dt.datetime(2026, 9, 21, 12, 0, 20, 500_000, tzinfo=dt.UTC)
        assert to_iso(exact) < to_iso(later)

    async def test_the_cache_guard_respects_it(self, redis_client):
        exact = to_iso(dt.datetime(2026, 9, 21, 12, 0, 20, 0, tzinfo=dt.UTC))
        later = to_iso(dt.datetime(2026, 9, 21, 12, 0, 20, 500_000, tzinfo=dt.UTC))

        assert await cache.set_price_if_newer(
            "H1", {"price": 1.0, "effective_at": later}, later, 60
        )
        assert not await cache.set_price_if_newer(
            "H1", {"price": 2.0, "effective_at": exact}, exact, 60
        ), "a timestamp at .000000 overwrote a newer one from the same second"

        assert (await cache.get_prices(["H1"]))["H1"]["price"] == 1.0


class TestM4PipelinedWrites:
    async def test_batch_write_reports_written_and_rejected(self, redis_client):
        old = to_iso(dt.datetime(2026, 9, 21, 11, 0, 0, tzinfo=dt.UTC))
        new = to_iso(dt.datetime(2026, 9, 21, 12, 0, 0, tzinfo=dt.UTC))

        await cache.set_price_if_newer("A", {"price": 1.0, "effective_at": new}, new, 60)

        written, rejected = await cache.set_prices_if_newer(
            [
                ("A", {"price": 9.0, "effective_at": old}, old),  # older: rejected
                ("B", {"price": 2.0, "effective_at": new}, new),  # new key: written
                ("C", {"price": 3.0, "effective_at": new}, new),  # new key: written
            ],
            ttl_seconds=60,
        )

        assert (written, rejected) == (2, 1)
        prices = await cache.get_prices(["A", "B", "C"])
        assert prices["A"]["price"] == 1.0
        assert prices["B"]["price"] == 2.0
