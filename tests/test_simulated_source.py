"""The simulated source random-walks from real captured prices.

It exists because the vendor cannot drive load scenarios, because equities are
frozen outside market hours so a weekend demo shows nothing moving, and because
a controlled comparison needs identical price movement on both sides of a run.
"""

import csv
import pathlib

import pytest

from watchlist.price_service.sources.simulated import SimulatedSource
from watchlist.shared.settings import get_settings

SEED_PRICES = pathlib.Path(__file__).resolve().parents[1] / "seed" / "prices.csv"

STARTING = {"NVDA": 222.27, "AAPL": 336.13, "ACB": 3.87}


@pytest.fixture(autouse=True)
def _always_move(monkeypatch):
    """Most tests want every ticker to move; change ratio is tested on its own."""
    monkeypatch.setattr(get_settings(), "sim_change_ratio", 1.0)


def _drain(source, ticks: int, tickers=None) -> list[dict]:
    import asyncio

    tickers = tickers or list(STARTING)
    return [asyncio.run(source.fetch(tickers)) for _ in range(ticks)]


def test_prices_stay_near_their_real_starting_values():
    """A penny stock must stay in cents and NVDA in the hundreds."""
    source = SimulatedSource(STARTING)
    final = _drain(source, 200)[-1]

    for ticker, start in STARTING.items():
        assert 0.5 * start < final[ticker] < 2.0 * start, f"{ticker} walked away from {start}"


def test_the_same_seed_reproduces_the_same_walk():
    """Scenario B is only a controlled comparison if both halves see this."""
    first = _drain(SimulatedSource(STARTING), 20)
    second = _drain(SimulatedSource(STARTING), 20)
    assert first == second


def test_a_price_never_goes_to_zero_or_negative(monkeypatch):
    monkeypatch.setattr(get_settings(), "sim_volatility", 5.0)
    source = SimulatedSource({"PENNY": 0.02})
    for prices in _drain(source, 300, ["PENNY"]):
        assert prices["PENNY"] > 0


def test_change_ratio_limits_how_many_tickers_move(monkeypatch):
    """The main lever on publish volume in every load scenario."""
    monkeypatch.setattr(get_settings(), "sim_change_ratio", 0.0)
    source = SimulatedSource(STARTING)
    assert _drain(source, 5)[-1] == STARTING


def test_an_unknown_ticker_is_omitted_not_invented():
    source = SimulatedSource(STARTING)
    prices = _drain(source, 1, ["NVDA", "NOSUCHTICKER"])[0]
    assert "NOSUCHTICKER" not in prices


def test_the_committed_seed_file_covers_the_catalog():
    assert SEED_PRICES.exists(), "run 'make capture-prices'"
    with SEED_PRICES.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) > 90
    assert all(float(r["price"]) > 0 for r in rows)
