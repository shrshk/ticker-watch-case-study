"""The handler layer: business logic, and the domain errors it raises.

Handlers raise domain errors rather than HTTPException, so these tests import
no FastAPI. That is the point of the layer - if this file needed a TestClient,
the logic would still be living in the routers.
"""

import datetime as dt

import pytest

from watchlist.modules.auth import auth_handler
from watchlist.modules.prices import prices_controller
from watchlist.modules.securities import securities_controller, securities_handler
from watchlist.modules.watchlist import watchlist_handler
from watchlist.modules.watchlist.watchlist_handler import WatchlistView

EFFECTIVE_AT = dt.datetime(2026, 9, 21, 12, 0, 0, tzinfo=dt.UTC)

CATALOG = [("NVDA", "NVIDIA"), ("AAPL", "Apple"), ("ATVI", "Activision Blizzard")]


@pytest.fixture
async def catalog(conn) -> dict[str, int]:
    await securities_controller.upsert_many(conn, CATALOG)
    rows = await securities_controller.all_tickers(conn)
    return {r["ticker"]: r["id"] for r in rows}


@pytest.fixture
async def user(conn):
    session = await auth_handler.register(conn, "handler_user", "pw", None, "Handler", "User")
    return session.user


class TestAuthHandler:
    async def test_register_returns_a_usable_session(self, conn):
        session = await auth_handler.register(conn, "alice", "pw")
        assert session.user.username == "alice"
        assert session.access_token
        assert session.expires_in > 0

    async def test_register_twice_raises_the_domain_error(self, conn):
        await auth_handler.register(conn, "bob", "pw")
        with pytest.raises(auth_handler.UsernameTakenError):
            await auth_handler.register(conn, "bob", "pw")

    async def test_login_succeeds_with_the_right_password(self, conn):
        await auth_handler.register(conn, "carol", "correct")
        session = await auth_handler.login(conn, "carol", "correct")
        assert session.user.username == "carol"

    async def test_login_fails_with_the_wrong_password(self, conn):
        await auth_handler.register(conn, "dave", "correct")
        with pytest.raises(auth_handler.InvalidCredentialsError):
            await auth_handler.login(conn, "dave", "wrong")

    async def test_an_unknown_user_raises_the_same_error_as_a_bad_password(self, conn):
        """Distinguishing them would let a caller enumerate usernames."""
        with pytest.raises(auth_handler.InvalidCredentialsError):
            await auth_handler.login(conn, "nobody", "whatever")

    async def test_get_user_returns_none_for_a_missing_id(self, conn):
        assert await auth_handler.get_user(conn, 10_000_000) is None


class TestSecuritiesHandler:
    async def test_search_returns_schema_objects(self, conn, catalog):
        results = await securities_handler.search(conn, "NV")
        assert [r.ticker for r in results] == ["NVDA"]

    async def test_search_trims_whitespace(self, conn, catalog):
        assert [r.ticker for r in await securities_handler.search(conn, "  AAPL  ")] == ["AAPL"]


class TestWatchlistHandler:
    async def test_a_new_user_has_an_empty_watchlist(self, conn, user):
        result = await watchlist_handler.get_watchlist(conn, user.id)
        assert result.items == []

    async def test_add_then_read_back(self, conn, user, catalog, redis_client):
        await watchlist_handler.add_item(conn, user.id, catalog["NVDA"])
        result = await watchlist_handler.get_watchlist(conn, user.id)
        assert [i.ticker for i in result.items] == ["NVDA"]

    async def test_adding_an_unknown_security_raises(self, conn, user):
        with pytest.raises(watchlist_handler.SecurityNotFoundError):
            await watchlist_handler.add_item(conn, user.id, 999_999)

    async def test_adding_twice_is_idempotent(self, conn, user, catalog, redis_client):
        await watchlist_handler.add_item(conn, user.id, catalog["NVDA"])
        await watchlist_handler.add_item(conn, user.id, catalog["NVDA"])
        result = await watchlist_handler.get_watchlist(conn, user.id)
        assert len(result.items) == 1

    async def test_removing_something_not_on_the_list_raises(self, conn, user, catalog):
        with pytest.raises(watchlist_handler.NotOnWatchlistError):
            await watchlist_handler.remove_item(conn, user.id, catalog["NVDA"])

    async def test_remove_takes_it_off(self, conn, user, catalog, redis_client):
        await watchlist_handler.add_item(conn, user.id, catalog["NVDA"])
        await watchlist_handler.remove_item(conn, user.id, catalog["NVDA"])
        result = await watchlist_handler.get_watchlist(conn, user.id)
        assert result.items == []

    async def test_one_user_cannot_see_another_users_watchlist(self, conn, catalog, redis_client):
        first = (await auth_handler.register(conn, "owner", "pw")).user
        second = (await auth_handler.register(conn, "stranger", "pw")).user
        await watchlist_handler.add_item(conn, first.id, catalog["NVDA"])

        assert len((await watchlist_handler.get_watchlist(conn, first.id)).items) == 1
        assert (await watchlist_handler.get_watchlist(conn, second.id)).items == []


class TestWatchlistView:
    """The view carries read provenance from the price read into the response."""

    async def test_it_reports_the_read_path_and_cache_split(
        self, conn, user, catalog, redis_client
    ):
        await watchlist_handler.add_item(conn, user.id, catalog["NVDA"])
        await prices_controller.upsert_many(conn, [(catalog["NVDA"], 222.27, EFFECTIVE_AT, "api")])

        result = await WatchlistView(conn, user.id).build()

        assert result.read_path == "redis"
        assert result.cache_misses == 1, "price was only in postgres, so this must be a miss"
        assert result.items[0].price == 222.27
        assert result.items[0].source == "api"

    async def test_a_security_with_no_price_still_appears(self, conn, user, catalog, redis_client):
        """ATVI is in the catalog and has no price. The row must not vanish."""
        await watchlist_handler.add_item(conn, user.id, catalog["ATVI"])

        result = await WatchlistView(conn, user.id).build()

        assert [i.ticker for i in result.items] == ["ATVI"]
        assert result.items[0].price is None
        assert result.items[0].source is None

    async def test_as_of_is_stamped(self, conn, user):
        result = await WatchlistView(conn, user.id).build()
        assert result.as_of.endswith("Z")
        dt.datetime.fromisoformat(result.as_of.replace("Z", "+00:00"))
