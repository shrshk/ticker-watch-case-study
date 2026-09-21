"""A random walk over real starting prices.

Not invented numbers: `last` is initialised from whatever the vendor last
reported (latest_prices, or seed/prices.csv). That keeps every ticker near its
real value - NVDA in the hundreds, a penny stock in cents - so screenshots look
plausible and log lines are readable.

SIM_CHANGE_RATIO matters more than it looks. It is the difference between
publishing 99 updates per tick and publishing 30, and it is the main lever on
egress in every load scenario. Report it alongside any benchmark.
"""

import random

from watchlist.price_service.sources.base import PriceSource
from watchlist.shared.logging import get_logger
from watchlist.shared.settings import get_settings

logger = get_logger(__name__)


class SimulatedSource(PriceSource):
    name = "simulated"

    def __init__(self, starting_prices: dict[str, float]) -> None:
        settings = get_settings()
        self._last = dict(starting_prices)
        self._volatility = settings.sim_volatility
        self._change_ratio = settings.sim_change_ratio
        # Seeded so a benchmark's two halves see identical price movement.
        self._rng = random.Random(settings.sim_seed)
        logger.info(
            "simulated source: %d starting prices, volatility=%s change_ratio=%s seed=%s",
            len(self._last),
            self._volatility,
            self._change_ratio,
            settings.sim_seed,
        )

    async def fetch(self, tickers: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for ticker in tickers:
            last = self._last.get(ticker)
            if last is None:
                continue
            if self._rng.random() < self._change_ratio:
                moved = last * (1 + self._rng.gauss(0, self._volatility))
                # Never walk a price to zero or negative.
                last = max(round(moved, 2), 0.01)
                self._last[ticker] = last
            out[ticker] = last
        return out
