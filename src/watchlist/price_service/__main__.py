"""Entry point: `python -m watchlist.price_service`."""

import asyncio
import contextlib

from watchlist.price_service.service import PriceService, SourceMismatch
from watchlist.shared.logging import configure_logging
from watchlist.shared.settings import get_settings

logger = configure_logging("price-service")

MISCONFIGURED_BACKOFF_SECONDS = 30


async def main() -> None:
    settings = get_settings()
    service = PriceService()
    try:
        await service.start()
    except SourceMismatch as exc:
        # Restarting will not fix this - an operator has to run reset-prices.
        # Pause before exiting so the container restart policy does not turn a
        # configuration error into a hot crash loop.
        logger.error("%s", exc)
        await asyncio.sleep(MISCONFIGURED_BACKOFF_SECONDS)
        raise SystemExit(1) from exc

    logger.info(
        "publishing every %.1fs from source=%s (upstream polled every %.1fs)",
        settings.publish_interval_seconds,
        settings.price_source,
        settings.upstream_poll_interval_seconds,
    )
    try:
        await service.run_forever()
    finally:
        await service.stop()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
