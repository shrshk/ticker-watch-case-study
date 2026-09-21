"""Starting a price source against rows another source wrote.

The two directions are not symmetric. Starting the vendor over simulated rows
must wipe them - they carry fresh timestamps and would silently beat every
real price on the effective_at guard. Starting the simulator over real rows
must keep them - they are exactly what the walk should seed from.
"""

import datetime as dt

from watchlist.modules.prices import prices_controller, prices_handler

AT = dt.datetime(2026, 9, 21, 12, 0, 0, tzinfo=dt.UTC)


async def _security(conn, ticker: str) -> int:
    return await conn.fetchval(
        "INSERT INTO securities (ticker, name) VALUES ($1, $2) RETURNING id", ticker, ticker
    )


async def _sources(conn) -> dict[str, int]:
    rows = await conn.fetch("SELECT source, count(*) AS n FROM latest_prices GROUP BY source")
    return {r["source"]: r["n"] for r in rows}


async def test_nothing_to_do_when_sources_agree(conn):
    sid = await _security(conn, "AGREE")
    await prices_controller.upsert_many(conn, [(sid, 1.0, AT, "api")])

    outcome = await prices_handler.reconcile_source(conn, "api")

    assert outcome.action == "clean"
    assert await _sources(conn) == {"api": 1}


async def test_empty_table_is_clean_for_either_source(conn):
    assert (await prices_handler.reconcile_source(conn, "api")).action == "clean"
    assert (await prices_handler.reconcile_source(conn, "simulated")).action == "clean"


async def test_starting_the_vendor_wipes_simulated_rows(conn):
    """The dangerous direction. Simulated rows would win the guard forever."""
    a = await _security(conn, "SIMA")
    b = await _security(conn, "SIMB")
    await prices_controller.upsert_many(
        conn, [(a, 100.0, AT, "simulated"), (b, 200.0, AT, "simulated")]
    )

    outcome = await prices_handler.reconcile_source(conn, "api")

    assert outcome.action == "wiped_simulated"
    assert outcome.rows == 2
    assert await _sources(conn) == {}


async def test_starting_the_vendor_keeps_existing_real_rows(conn):
    """Mixed table: only the simulated rows go. Real quotes are worth keeping."""
    real = await _security(conn, "REAL")
    fake = await _security(conn, "FAKE")
    await prices_controller.upsert_many(
        conn, [(real, 10.0, AT, "api"), (fake, 20.0, AT, "simulated")]
    )

    outcome = await prices_handler.reconcile_source(conn, "api")

    assert outcome.action == "wiped_simulated"
    assert outcome.rows == 1
    assert await _sources(conn) == {"api": 1}


async def test_starting_the_simulator_adopts_real_rows(conn):
    """The safe direction. The last real quote is the right starting value, and
    the rows are re-stamped so table, cache and badge agree from tick one."""
    a = await _security(conn, "ADOPTA")
    b = await _security(conn, "ADOPTB")
    await prices_controller.upsert_many(conn, [(a, 336.13, AT, "api"), (b, 222.27, AT, "api")])

    outcome = await prices_handler.reconcile_source(conn, "simulated")

    assert outcome.action == "adopted_api"
    assert outcome.rows == 2
    assert await _sources(conn) == {"simulated": 2}
    # The values are untouched - that is the whole point of adopting them.
    prices = {r["security_id"]: r["price"] for r in await prices_controller.get_many(conn, [a, b])}
    assert prices == {a: 336.13, b: 222.27}


async def test_adopting_does_not_advance_effective_at(conn):
    """Re-stamping the source must not make the rows look newer than they are,
    or a later real quote with an honest timestamp would lose the guard."""
    sid = await _security(conn, "STAMP")
    await prices_controller.upsert_many(conn, [(sid, 1.0, AT, "api")])

    await prices_handler.reconcile_source(conn, "simulated")

    effective_at = await conn.fetchval(
        "SELECT effective_at FROM latest_prices WHERE security_id = $1", sid
    )
    assert effective_at == AT
