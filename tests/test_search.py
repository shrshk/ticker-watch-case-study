"""Search must handle NV, NVDA and NVIDIA, and rank sensibly.

Real symbols matter here: NV matches NVDA and NVAX and NVR, which a set of
invented tickers would not exercise.
"""

import pytest

from watchlist.modules.securities import securities_controller

CATALOG = [
    ("NVDA", "NVIDIA"),
    ("NVAX", "Novavax"),
    ("NVR", "NVR Inc"),
    ("AAPL", "Apple"),
    ("TSLA", "Tesla"),
]


@pytest.fixture
async def catalog(conn):
    await securities_controller.upsert_many(conn, CATALOG)
    return conn


async def test_prefix_matches_every_nv_ticker(catalog):
    found = [r["ticker"] for r in await securities_controller.search(catalog, "NV")]
    assert set(found) == {"NVDA", "NVAX", "NVR"}


async def test_exact_ticker_ranks_first(catalog):
    found = [r["ticker"] for r in await securities_controller.search(catalog, "NVDA")]
    assert found[0] == "NVDA"


async def test_company_name_matches(catalog):
    found = [r["ticker"] for r in await securities_controller.search(catalog, "nvidia")]
    assert found == ["NVDA"]


async def test_search_is_case_insensitive(catalog):
    lower = [r["ticker"] for r in await securities_controller.search(catalog, "aapl")]
    upper = [r["ticker"] for r in await securities_controller.search(catalog, "AAPL")]
    assert lower == upper == ["AAPL"]


async def test_a_partial_company_name_matches(catalog):
    found = [r["ticker"] for r in await securities_controller.search(catalog, "Nova")]
    assert found == ["NVAX"]


async def test_no_match_returns_nothing(catalog):
    assert await securities_controller.search(catalog, "zzzznotathing") == []


async def test_limit_is_honoured(catalog):
    assert len(await securities_controller.search(catalog, "NV", limit=2)) == 2
