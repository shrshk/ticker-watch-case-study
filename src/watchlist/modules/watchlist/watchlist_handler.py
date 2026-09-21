"""Watchlist membership and the price snapshot.

`GET /watchlist` returns membership and prices together. Two round trips on app
load is a worse story than one, and it keeps the phase 3 subscribe-ordering
rule simple: subscribe, then take one snapshot, then drain.
"""

import asyncpg

from watchlist.modules.auth import auth_controller
from watchlist.modules.prices.prices_handler import SnapshotReader
from watchlist.modules.securities import securities_controller
from watchlist.modules.watchlist import watchlist_controller
from watchlist.modules.watchlist.watchlist_schema import Watchlist, WatchlistItem
from watchlist.shared.logging import get_logger
from watchlist.shared.timeutil import now_iso

logger = get_logger(__name__)


class SecurityNotFoundError(Exception):
    """No security with that id."""


class NotOnWatchlistError(Exception):
    """The security is not on this user's watchlist."""


class WatchlistView:
    """Assemble one user's watchlist with current prices.

    One instance per request. Three steps that each need the previous one's
    output - resolve the watchlist, load its members, price them - plus the
    read provenance that has to survive from the price read into the response.
    Holding that on an instance keeps `build` readable as the sequence it is.
    """

    def __init__(self, conn: asyncpg.Connection, user_id: int) -> None:
        self._conn = conn
        self._user_id = user_id
        self._watchlist_id: int | None = None
        self._members: list[asyncpg.Record] = []

    async def build(self) -> Watchlist:
        await self._resolve_watchlist()
        await self._load_members()
        snapshot = await self._load_prices()

        return Watchlist(
            items=[self._to_item(row, snapshot) for row in self._members],
            read_path=snapshot.read_path,
            cache_hits=snapshot.cache_hits,
            cache_misses=snapshot.cache_misses,
            as_of=now_iso(),
        )

    async def _resolve_watchlist(self) -> None:
        self._watchlist_id = await auth_controller.default_watchlist_id(self._conn, self._user_id)

    async def _load_members(self) -> None:
        self._members = await watchlist_controller.members(self._conn, self._watchlist_id)

    async def _load_prices(self):
        pairs = [(r["id"], r["ticker"]) for r in self._members]
        return await SnapshotReader(self._conn).read(pairs)

    @staticmethod
    def _to_item(row: asyncpg.Record, snapshot) -> WatchlistItem:
        price = snapshot.prices.get(row["id"])
        return WatchlistItem(
            id=row["id"],
            ticker=row["ticker"],
            name=row["name"],
            exchange=row["exchange"],
            price=price.price if price else None,
            effective_at=price.effective_at if price else None,
            # A security the vendor lists but cannot price - ATVI, delisted -
            # has no row here. The client renders a dash rather than a zero.
            source=price.source if price else None,
        )


async def get_watchlist(conn: asyncpg.Connection, user_id: int) -> Watchlist:
    return await WatchlistView(conn, user_id).build()


async def add_item(conn: asyncpg.Connection, user_id: int, security_id: int) -> None:
    if await securities_controller.get(conn, security_id) is None:
        raise SecurityNotFoundError(security_id)
    watchlist_id = await auth_controller.default_watchlist_id(conn, user_id)
    await watchlist_controller.add(conn, watchlist_id, security_id)


async def remove_item(conn: asyncpg.Connection, user_id: int, security_id: int) -> None:
    watchlist_id = await auth_controller.default_watchlist_id(conn, user_id)
    if not await watchlist_controller.remove(conn, watchlist_id, security_id):
        raise NotOnWatchlistError(security_id)
