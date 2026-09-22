"""One-off: capture the vendor catalog and a price snapshot into seed/.

Run with `make capture-prices`, commit the output, note the date in the README.
These files are not a runtime dependency and not a backup. They exist so that:

  * simulated mode random-walks from real starting values, not invented ones
  * benchmarks are reproducible from a file in git rather than from whatever
    the market did that day
  * the stack boots with a usable catalog when the vendor is unreachable
"""

import asyncio
import csv
import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

import httpx  # noqa: E402

from watchlist.shared.settings import get_settings  # noqa: E402

SEED_DIR = pathlib.Path(__file__).resolve().parents[2] / "seed"


async def main() -> None:
    settings = get_settings()
    if not settings.vendor_api_key or not settings.vendor_api_base:
        raise SystemExit("VENDOR_API_KEY and VENDOR_API_BASE are not set")

    SEED_DIR.mkdir(exist_ok=True)
    async with httpx.AsyncClient(
        base_url=settings.vendor_api_base,
        headers={settings.vendor_api_key_header: settings.vendor_api_key},
        timeout=30.0,
    ) as client:
        catalog = (await client.get("/stock/tickers/")).raise_for_status().json()
        tickers = sorted(catalog)
        print(f"catalog: {len(tickers)} tickers")

        # The prices endpoint takes the whole universe in one query string.
        prices = (
            (await client.get("/stock/prices/", params={"tickers": ",".join(tickers)}))
            .raise_for_status()
            .json()
        )
        print(f"prices: {len(prices)} quotes in 1 call")

    captured_at = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")

    with (SEED_DIR / "securities.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "name"])
        writer.writerows((t, catalog[t]) for t in tickers)

    # The vendor lists tickers it cannot price - ATVI, delisted after the
    # Microsoft acquisition, comes back as null. Omit them rather than write an
    # empty cell that later parses as a crash.
    priced = [t for t in tickers if prices.get(t) is not None]
    if len(priced) != len(tickers):
        missing = sorted(set(tickers) - set(priced))
        print(f"no price for {len(missing)} ticker(s), omitted: {', '.join(missing)}")

    with (SEED_DIR / "prices.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "price", "captured_at"])
        writer.writerows((t, prices[t], captured_at) for t in priced)

    print(f"wrote seed/securities.csv and seed/prices.csv (captured_at={captured_at})")


if __name__ == "__main__":
    asyncio.run(main())
