"""The bundled vendor honours the contract the adapter was written against.

Each case here is something the original third-party API actually did, and
something the price service has code for. Losing one would leave that code
untested.
"""

import httpx
import pytest

from watchlist.shared.settings import get_settings
from watchlist.vendor_mock.main import app


@pytest.fixture
async def vendor():
    settings = get_settings()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://vendor",
        headers={settings.vendor_api_key_header: settings.vendor_api_key},
    ) as client:
        yield client


async def test_catalog_is_the_seeded_universe(vendor):
    body = (await vendor.get("/stock/tickers/")).json()
    assert len(body) == 99
    assert body["AAPL"] == "Apple"
    assert "ATVI" in body, "the delisted ticker stays listed - that is the whole edge case"


async def test_prices_for_a_batch(vendor):
    body = (await vendor.get("/stock/prices/", params={"tickers": "AAPL,NVDA"})).json()
    assert set(body) == {"AAPL", "NVDA"}
    assert all(isinstance(v, float) and v > 0 for v in body.values())


async def test_listed_but_unpriced_ticker_is_null(vendor):
    body = (await vendor.get("/stock/prices/", params={"tickers": "AAPL,ATVI"})).json()
    assert body["ATVI"] is None
    assert body["AAPL"] is not None


async def test_one_unknown_ticker_fails_the_whole_batch(vendor):
    response = await vendor.get("/stock/prices/", params={"tickers": "AAPL,FAKE1"})
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid ticker"}


async def test_requests_without_the_key_are_refused():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://vendor"
    ) as anonymous:
        assert (await anonymous.get("/stock/tickers/")).status_code == 403
        assert (
            await anonymous.get("/stock/prices/", params={"tickers": "AAPL"})
        ).status_code == 403
        wrong = await anonymous.get("/stock/tickers/", headers={"X-API-Key": "nope"})
        assert wrong.status_code == 403


async def test_prices_move_between_calls(vendor):
    """A walk, not a constant: the change-detection path needs something to detect."""
    first = (await vendor.get("/stock/prices/", params={"tickers": "AAPL,NVDA,AMZN,MSFT"})).json()
    changed = False
    for _ in range(20):
        later = (
            await vendor.get("/stock/prices/", params={"tickers": "AAPL,NVDA,AMZN,MSFT"})
        ).json()
        if later != first:
            changed = True
            break
    assert changed
