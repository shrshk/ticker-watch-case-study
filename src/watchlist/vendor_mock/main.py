"""A stand-in for the third-party quote API.

The product treats its price vendor as a paid external service: one caller,
one call per tick, every call counted. This service is that vendor for the
repo. It serves the contract the adapter speaks - the catalog, prices for a
comma-separated list, an API key in a header - and reproduces the edge cases
the real one had: a ticker listed in the catalog but never priced (delisted),
a 400 for the whole batch when any ticker is unknown, a 403 without the key.

Prices random-walk from seed/prices.csv on every call, so nothing here depends
on market hours. State is in-process: a restart begins the walk again from the
seed. It is not the simulator - that bypasses the adapter; this exercises it.
"""

import csv
import pathlib

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from watchlist.price_service.sources.simulated import SimulatedSource
from watchlist.shared.logging import configure_logging, get_logger
from watchlist.shared.settings import get_settings

SEED_DIR = pathlib.Path(__file__).resolve().parents[3] / "seed"

logger = get_logger(__name__)


def _read_csv(name: str) -> list[dict[str, str]]:
    with (SEED_DIR / name).open() as fh:
        return list(csv.DictReader(fh))


class VendorMock:
    """The catalog and a walking price book behind it."""

    def __init__(self) -> None:
        self.catalog = {r["ticker"]: r["name"] for r in _read_csv("securities.csv")}
        starting = {r["ticker"]: float(r["price"]) for r in _read_csv("prices.csv")}
        # Listed but never priced - the delisted-ticker case the adapter must skip.
        self.unpriced = sorted(set(self.catalog) - set(starting))
        self._book = SimulatedSource(starting)
        logger.info(
            "vendor stand-in: %d tickers, %d unpriced (%s)",
            len(self.catalog),
            len(self.unpriced),
            ",".join(self.unpriced) or "-",
        )

    async def prices(self, tickers: list[str]) -> dict[str, float | None]:
        unknown = [t for t in tickers if t not in self.catalog]
        if unknown:
            # The real vendor rejected the whole request, not just the bad symbol.
            raise HTTPException(status_code=400, detail="Invalid ticker")
        priced = await self._book.fetch(tickers)
        return {t: priced.get(t) for t in tickers}


configure_logging("vendor")
app = FastAPI(title="Quote vendor (stand-in)", docs_url=None, redoc_url=None)
vendor = VendorMock()


async def require_key(request: Request) -> None:
    settings = get_settings()
    if request.headers.get(settings.vendor_api_key_header) != settings.vendor_api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


@app.get("/health")
async def health() -> dict[str, int]:
    return {"tickers": len(vendor.catalog)}


@app.get("/stock/tickers/", dependencies=[Depends(require_key)])
async def tickers() -> dict[str, str]:
    return vendor.catalog


@app.get("/stock/prices/", dependencies=[Depends(require_key)])
async def prices(tickers: str = Query("")) -> dict[str, float | None]:
    wanted = [t.strip() for t in tickers.split(",") if t.strip()]
    return await vendor.prices(wanted)
