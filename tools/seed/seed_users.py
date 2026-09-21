"""Bulk-seed users, watchlists and watchlist membership.

Presets exist to answer one question: can Postgres carry a million users with
ten million watchlist rows, and what does the snapshot read cost at that size.

    preset   users     watchlist rows
    small    10,000    ~100,000
    medium   100,000   ~1,000,000
    million  1,000,000 ~10,000,000

Everything is loaded with COPY. Row-by-row inserts at ten million rows are not
slow, they are a different afternoon.
"""

import argparse
import asyncio
import math
import pathlib
import random
import sys
import time
from collections.abc import Iterator

import asyncpg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))

from watchlist.shared.security import hash_password  # noqa: E402
from watchlist.shared.settings import get_settings  # noqa: E402

PRESETS = {
    "small": 10_000,
    "medium": 100_000,
    "million": 1_000_000,
}

ITEMS_PER_USER = 10
# Sample a few extra per user so that de-duplicating a skewed draw still
# usually leaves ITEMS_PER_USER distinct securities.
OVERSAMPLE = 6
COPY_BATCH = 50_000

# Stock popularity is heavily skewed. s=1.1 puts the most-watched ticker on
# roughly a tenth of all watchlist rows and leaves a long tail.
ZIPF_EXPONENT = 1.1

# Which ticker gets which rank matters as much as the shape of the curve.
# Ranking by security id means ranking alphabetically, which made Airbnb the
# second most-watched stock and left NVDA in the tail - a distribution that is
# statistically fine and visibly absurd, and useless for naming a celebrity
# ticker in a fanout test.
#
# Rough retail-attention order. Tickers not listed keep a deterministic
# shuffled order behind these, so the tail is not alphabetical either.
POPULARITY = [
    "NVDA",
    "TSLA",
    "AAPL",
    "AMZN",
    "META",
    "MSFT",
    "GOOG",
    "AMD",
    "PLTR",
    "COIN",
    "GME",
    "NFLX",
    "INTC",
    "HOOD",
    "SHOP",
    "UBER",
    "DIS",
    "SNAP",
    "SPOT",
    "NIO",
    "F",
    "BAC",
    "PYPL",
    "MRNA",
    "ABNB",
    "LYFT",
    "AFRM",
    "SBUX",
    "BYND",
    "PTON",
    "T",
    "KO",
]


def zipf_cum_weights(n: int, exponent: float = ZIPF_EXPONENT) -> list[float]:
    total = 0.0
    cumulative = []
    for rank in range(1, n + 1):
        total += 1.0 / rank**exponent
        cumulative.append(total)
    return cumulative


def rank_securities(rows, seed: int) -> list[int]:
    """Order security ids by popularity, most-watched first.

    The Zipf draw assigns weight by position, so this decides which ticker is
    the celebrity. Listed tickers take their listed rank; everything else is
    shuffled deterministically behind them rather than left alphabetical.
    """
    by_ticker = {r["ticker"]: r["id"] for r in rows}

    ranked = [by_ticker[t] for t in POPULARITY if t in by_ticker]
    remainder = [r["id"] for r in rows if r["id"] not in set(ranked)]
    random.Random(seed).shuffle(remainder)
    return ranked + remainder


async def pad_catalog(conn: asyncpg.Connection, target: int) -> int:
    """Add synthetic securities so catalog size can be varied independently.

    Only 99 real securities exist behind the vendor API, which is enough to
    exercise search and the product but not enough to size a database. Padding
    is flagged is_synthetic so it can never be mistaken for a real listing.
    """
    existing = await conn.fetchval("SELECT count(*) FROM securities")
    if existing >= target:
        return 0

    rows = [
        (f"SYN{i:05d}", f"Synthetic Holdings {i:05d}", True)
        for i in range(1, target - existing + 1)
    ]
    await conn.copy_records_to_table(
        "securities", records=rows, columns=["ticker", "name", "is_synthetic"]
    )
    return len(rows)


def user_records(count: int, password_hash: str, offset: int) -> Iterator[tuple]:
    for i in range(offset + 1, offset + count + 1):
        yield (f"load_user_{i}", f"load_user_{i}@casestudy.com", "Load", "User", password_hash)


def item_records(
    watchlist_ids: list[int],
    security_ids: list[int],
    cum_weights: list[float],
    rng: random.Random,
) -> Iterator[tuple]:
    draw = rng.choices
    for watchlist_id in watchlist_ids:
        picked = dict.fromkeys(
            draw(security_ids, cum_weights=cum_weights, k=ITEMS_PER_USER + OVERSAMPLE)
        )
        for security_id in list(picked)[:ITEMS_PER_USER]:
            yield (watchlist_id, security_id)


async def seed(preset: str, catalog_size: int | None, truncate: bool) -> None:
    users_wanted = PRESETS[preset]
    settings = get_settings()
    conn = await asyncpg.connect(dsn=settings.postgres_dsn, command_timeout=1800)

    try:
        if truncate:
            print("clearing previously seeded load users")
            await conn.execute("DELETE FROM users WHERE username LIKE 'load\\_user\\_%'")

        if catalog_size:
            added = await pad_catalog(conn, catalog_size)
            print(f"catalog: added {added:,} synthetic securities")

        rows = await conn.fetch("SELECT id, ticker FROM securities ORDER BY id")
        if not rows:
            raise SystemExit("no securities; run 'make migrate' and start the price service")
        security_ids = rank_securities(rows, settings.sim_seed)
        print(f"catalog: {len(security_ids):,} securities")

        # One bcrypt hash, reused for every seeded user. bcrypt is deliberately
        # slow - about 250ms each - so hashing a million would take three days.
        # These accounts exist to occupy rows, not to be logged into
        # individually; the demo users get their own real hashes.
        shared_hash = hash_password("loadtest")
        cum_weights = zipf_cum_weights(len(security_ids))
        rng = random.Random(settings.sim_seed)

        started = time.monotonic()
        offset = await conn.fetchval(
            "SELECT count(*) FROM users WHERE username LIKE 'load\\_user\\_%'"
        )
        remaining = users_wanted - offset
        if remaining <= 0:
            print(f"already seeded {offset:,} load users")
        else:
            print(f"seeding {remaining:,} users in batches of {COPY_BATCH:,}")

        done = 0
        while done < remaining:
            batch = min(COPY_BATCH, remaining - done)
            async with conn.transaction():
                await conn.copy_records_to_table(
                    "users",
                    records=user_records(batch, shared_hash, offset + done),
                    columns=["username", "email", "first_name", "last_name", "password_hash"],
                )
                new_user_ids = [
                    r["id"]
                    for r in await conn.fetch(
                        "SELECT id FROM users WHERE username LIKE 'load\\_user\\_%' "
                        "ORDER BY id DESC LIMIT $1",
                        batch,
                    )
                ]
                await conn.copy_records_to_table(
                    "watchlists",
                    records=((uid, "default") for uid in new_user_ids),
                    columns=["user_id", "name"],
                )
                watchlist_ids = [
                    r["id"]
                    for r in await conn.fetch(
                        "SELECT id FROM watchlists WHERE user_id = ANY($1::bigint[])", new_user_ids
                    )
                ]
                await conn.copy_records_to_table(
                    "watchlist_items",
                    records=item_records(watchlist_ids, security_ids, cum_weights, rng),
                    columns=["watchlist_id", "security_id"],
                )
            done += batch
            elapsed = time.monotonic() - started
            rate = done / elapsed if elapsed else 0
            eta = (remaining - done) / rate if rate else 0
            print(
                f"  {done:>9,} / {remaining:,} users  ({rate:,.0f}/s, {math.ceil(eta)}s remaining)",
                flush=True,
            )

        print("analyzing")
        for table in ("users", "watchlists", "watchlist_items", "securities"):
            await conn.execute(f"ANALYZE {table}")

        await report(conn)
        print(f"\ndone in {time.monotonic() - started:.1f}s")
    finally:
        await conn.close()


async def report(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        """
        SELECT relname AS table,
               to_char(n_live_tup, 'FM999,999,999') AS rows,
               pg_size_pretty(pg_total_relation_size(relid)) AS total_size
        FROM pg_stat_user_tables
        ORDER BY pg_total_relation_size(relid) DESC
        """
    )
    print(f"\n{'table':<20} {'rows':>14} {'size':>12}")
    print("-" * 48)
    for row in rows:
        print(f"{row['table']:<20} {row['rows']:>14} {row['total_size']:>12}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preset", choices=sorted(PRESETS))
    parser.add_argument(
        "--securities",
        type=int,
        default=None,
        help="pad the catalog to this many securities with synthetic rows",
    )
    parser.add_argument(
        "--truncate", action="store_true", help="delete previously seeded load users first"
    )
    args = parser.parse_args()
    asyncio.run(seed(args.preset, args.securities, args.truncate))


if __name__ == "__main__":
    main()
