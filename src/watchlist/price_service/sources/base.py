"""The price source interface.

Two implementations, both required. The vendor adapter is the third-party
integration - served in this repo by the bundled stand-in, which keeps the
contract and its edge cases - and proves the interface is not a toy. The
simulator exists because a paid vendor cannot drive load scenarios, because a
real one is frozen outside market hours, and because a controlled comparison
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
