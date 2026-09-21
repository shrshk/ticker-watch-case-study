"""Logging setup and the one way to get a logger.

Modules call `get_logger(__name__)` at module level; the service entry points
call `configure_logging` once. Routing every module through one accessor is
what makes a later swap to structured logging a single-file change.
"""

import logging
import os
import sys


def configure_logging(service: str) -> logging.Logger:
    """Install the process-wide handler. Called once, by a service entry point."""
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


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
