"""One-line log setup, shared by both services."""

import logging
import os
import sys


def configure_logging(service: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt=f"%(asctime)s %(levelname)-7s [{service}] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    # httpx logs every request at INFO, and the vendor call carries all 99
    # tickers in the query string. One tick would drown the tick log itself.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logging.getLogger(service)
