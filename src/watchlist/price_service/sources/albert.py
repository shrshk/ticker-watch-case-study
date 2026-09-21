"""The Albert case study API.

Treated as a third-party integration with per-call pricing, so the call count
is a design constraint rather than an afterthought:

  * one process (this one) is the only caller, no matter how many users exist
  * one call per tick covers the entire universe - the prices endpoint accepts
    every ticker in a single query string
  * the tick that calls the vendor is decoupled from the tick that updates
    clients, so vendor spend and the product's 5s promise move independently

At 99 tickers that is one call per interval, forever, for any number of users.
"""

import logging

import httpx

from watchlist.price_service.sources.base import PriceSource
from watchlist.shared.settings import get_settings

logger = logging.getLogger(__name__)


class AlbertSource(PriceSource):
    name = "api"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.albert_api_key:
            raise RuntimeError("ALBERT_API_KEY is not set; cannot use PRICE_SOURCE=api")
        self._client = httpx.AsyncClient(
            base_url=settings.albert_api_base,
            headers={"Albert-Case-Study-API-Key": settings.albert_api_key},
            timeout=10.0,
        )

    async def catalog(self) -> dict[str, str]:
        response = await self._client.get("/stock/tickers/")
        response.raise_for_status()
        return response.json()

    async def fetch(self, tickers: list[str]) -> dict[str, float]:
        if not tickers:
            return {}
        response = await self._client.get("/stock/prices/", params={"tickers": ",".join(tickers)})
        response.raise_for_status()
        payload = response.json()

        # The vendor returns null for a ticker it lists but cannot price - ATVI,
        # for one, which is in the catalog but delisted. Omit it rather than
        # invent a number; the client renders a missing price as a dash.
        priced = {t: float(v) for t, v in payload.items() if v is not None}
        unpriced = len(payload) - len(priced)
        if unpriced:
            logger.debug("vendor returned no price for %d of %d tickers", unpriced, len(payload))
        return priced

    async def aclose(self) -> None:
        await self._client.aclose()
