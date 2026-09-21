"""Entry point: `python -m watchlist.price_service`."""

import asyncio
import contextlib

from watchlist.price_service.service import PriceService
from watchlist.shared.logging import configure_logging
from watchlist.shared.settings import get_settings

logger = configure_logging("price-service")


async def main() -> None:
    settings = get_settings()
    service = PriceService()
    await service.start()

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
