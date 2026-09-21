"""Clear derived price state so the price source can be switched.

Prices are fully derived: latest_prices and the Redis cache can always be
rebuilt within one tick. Users, watchlists and securities are untouched, so
this costs nothing.
"""

import asyncio
import pathlib
import sys

import asyncpg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from watchlist.shared import cache  # noqa: E402
from watchlist.shared.settings import get_settings  # noqa: E402


async def main() -> None:
    conn = await asyncpg.connect(dsn=get_settings().postgres_dsn)
    try:
        result = await conn.execute("DELETE FROM latest_prices")
        print(f"postgres: {result}")
    finally:
        await conn.close()

    client = cache.client()
    deleted = 0
    async for key in client.scan_iter(match="price:*", count=1000):
        deleted += await client.delete(key)
    print(f"redis: deleted {deleted} price keys")
    await cache.close()


if __name__ == "__main__":
    asyncio.run(main())
