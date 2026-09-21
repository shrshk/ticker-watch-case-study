"""The price source interface.

Two implementations, both required. The vendor is the real integration and
proves the interface is not a toy. The simulator exists because the vendor
cannot drive load scenarios, because equities are frozen outside market hours
so a weekend demo shows nothing moving, and because a controlled comparison
needs identical price movement on both sides of a run.
"""

import abc


class PriceSource(abc.ABC):
    #: Recorded on every row it writes, and checked against latest_prices at startup.
    name: str

    @abc.abstractmethod
    async def fetch(self, tickers: list[str]) -> dict[str, float]:
        """Current price per ticker. Tickers the source does not know are omitted."""

    async def catalog(self) -> dict[str, str] | None:
        """Ticker -> company name, if this source can supply one."""
        return None

    async def aclose(self) -> None:
        return None
