"""Synthetic securities never reach the vendor.

`seed_users.py --securities N` pads the catalog with made-up tickers for load
tests. The vendor answers 400 for the whole batch if one of them is in the
request, so the api source has to price the real listings only. The simulator
does not care and prices everything.
"""

from watchlist.modules.securities import securities_controller


async def _seed(conn) -> None:
    await securities_controller.upsert_many(conn, [("REAL1", "Real One"), ("REAL2", "Real Two")])
    await conn.execute(
        "INSERT INTO securities (ticker, name, is_synthetic) VALUES ($1, $2, true)",
        "SYN00001",
        "Synthetic Holdings 00001",
    )


async def test_vendor_list_excludes_synthetic_rows(conn):
    await _seed(conn)

    rows = await securities_controller.all_tickers(conn, include_synthetic=False)

    assert [r["ticker"] for r in rows] == ["REAL1", "REAL2"]


async def test_default_list_includes_everything_for_the_simulator(conn):
    await _seed(conn)

    rows = await securities_controller.all_tickers(conn)

    assert [r["ticker"] for r in rows] == ["REAL1", "REAL2", "SYN00001"]


async def test_vendor_upserts_are_real_listings(conn):
    """The catalog sync must never flag a vendor ticker as synthetic, or the
    api source would silently stop pricing it."""
    await securities_controller.upsert_many(conn, [("AAPL", "Apple")])

    flagged = await conn.fetchval("SELECT is_synthetic FROM securities WHERE ticker = 'AAPL'")

    assert flagged is False
