"""Measure the database-side cost of the read paths at the current data size.

Answers two of the case study's questions directly:

  * Can Postgres support a million users and ten million watchlist rows?
  * What is watchlist snapshot latency at scale, cached and uncached?

This measures the *server* side only - no HTTP, no client. The load generator
measures what a client actually experiences; this isolates how much of that is
the database. Run it at each seed size to get a curve rather than a number.
"""

import argparse
import asyncio
import pathlib
import random
import statistics
import sys
import time

import asyncpg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from watchlist.shared import cache, db, price_reader  # noqa: E402
from watchlist.shared.repo import securities as security_repo  # noqa: E402
from watchlist.shared.repo import watchlists as watchlist_repo  # noqa: E402
from watchlist.shared.settings import get_settings  # noqa: E402

SEARCH_TERMS = ["NV", "AAPL", "tesla", "micro", "A", "GOOG", "bank", "SYN0"]


class Timings:
    def __init__(self, name: str) -> None:
        self.name = name
        self.samples: list[float] = []

    def record(self, seconds: float) -> None:
        self.samples.append(seconds * 1000)

    def summary(self) -> dict:
        if not self.samples:
            return {}
        ordered = sorted(self.samples)
        return {
            "n": len(ordered),
            "p50": statistics.median(ordered),
            "p95": ordered[int(len(ordered) * 0.95) - 1],
            "p99": ordered[int(len(ordered) * 0.99) - 1],
            "max": ordered[-1],
        }


async def sample_watchlists(conn: asyncpg.Connection, count: int) -> list[int]:
    """Random seeded watchlists. TABLESAMPLE keeps this cheap at ten million rows."""
    rows = await conn.fetch(
        "SELECT id FROM watchlists ORDER BY random() LIMIT $1",
        count,
    )
    return [r["id"] for r in rows]


async def run(iterations: int, warmup: int) -> None:
    settings = get_settings()
    await db.connect(min_size=4, max_size=8)
    await cache.register_scripts()

    async with db.pool().acquire() as conn:
        sizes = await conn.fetch(
            """
            SELECT relname AS name,
                   n_live_tup AS rows,
                   pg_size_pretty(pg_total_relation_size(relid)) AS size
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            """
        )
        print(f"{'table':<20} {'rows':>14} {'size':>12}")
        print("-" * 48)
        for row in sizes:
            print(f"{row['name']:<20} {row['rows']:>14,} {row['size']:>12}")
        print()

        watchlist_ids = await sample_watchlists(conn, iterations + warmup)

    search = Timings("securities search")
    membership = Timings("watchlist membership")
    snapshot_redis = Timings("snapshot (redis)")
    snapshot_pg = Timings("snapshot (postgres)")

    rng = random.Random(settings.sim_seed)

    async with db.pool().acquire() as conn:
        for i, watchlist_id in enumerate(watchlist_ids):
            measuring = i >= warmup

            term = rng.choice(SEARCH_TERMS)
            started = time.perf_counter()
            await security_repo.search(conn, term)
            if measuring:
                search.record(time.perf_counter() - started)

            started = time.perf_counter()
            members = await watchlist_repo.members(conn, watchlist_id)
            if measuring:
                membership.record(time.perf_counter() - started)

            pairs = [(r["id"], r["ticker"]) for r in members]

            settings.latest_price_source = "redis"
            started = time.perf_counter()
            await price_reader.read(conn, pairs)
            if measuring:
                snapshot_redis.record(time.perf_counter() - started)

            settings.latest_price_source = "postgres"
            started = time.perf_counter()
            await price_reader.read(conn, pairs)
            if measuring:
                snapshot_pg.record(time.perf_counter() - started)

    settings.latest_price_source = "redis"

    print(f"{'operation':<24} {'n':>6} {'p50':>9} {'p95':>9} {'p99':>9} {'max':>9}")
    print("-" * 72)
    for timing in (search, membership, snapshot_redis, snapshot_pg):
        s = timing.summary()
        print(
            f"{timing.name:<24} {s['n']:>6} {s['p50']:>8.2f}m {s['p95']:>8.2f}m "
            f"{s['p99']:>8.2f}m {s['max']:>8.2f}m"
        )
    print("\n(milliseconds; server-side only, no HTTP)")

    await cache.close()
    await db.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument("--warmup", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(run(args.iterations, args.warmup))


if __name__ == "__main__":
    main()
