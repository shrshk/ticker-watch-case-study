"""Stock search.

Postgres only. Elasticsearch would need a measured requirement to justify it,
and at this catalog size the ranked ILIKE query answers in a sixth of a
millisecond - see docs/measurements.md.
"""

import asyncpg

from watchlist.modules.securities import securities_controller
from watchlist.modules.securities.securities_schema import Security


async def search(conn: asyncpg.Connection, query: str, limit: int = 20) -> list[Security]:
    rows = await securities_controller.search(conn, query.strip(), limit)
    return [
        Security(id=r["id"], ticker=r["ticker"], name=r["name"], exchange=r["exchange"])
        for r in rows
    ]
