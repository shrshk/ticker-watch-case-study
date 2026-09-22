"""The price service: the only writer of prices anywhere in the system.

There is no external mutation path, so cache invalidation is not a problem to
solve here - but out-of-order writes are, and both writes are guarded.

Each publish tick:
  1. read current prices from the source adapter (vendor call rate-limited
     separately from the publish cadence)
  2. detect which tickers actually changed
  3. write *every* current price to the Redis cache, guarded on effective_at
  4. upsert *changed* prices into latest_prices, guarded on effective_at
  5.  publish *changed* tickers to Centrifugo

The three writes deliberately have different scopes. The cache is a picture of
what is current, so refreshing it every tick keeps a quiet ticker from expiring
and refills it after a Redis restart without waiting for a price to move.
latest_prices is the durable record of a price *change*, so rewriting unchanged
rows every 5 seconds would be pure churn. Publishing is per change by
definition - that is the reduction that makes push cheap.

`effective_at` therefore means "when this price was first seen at this value",
and both stores agree on it.

Steps 3 and 4 run concurrently. A Postgres stall must not stop the cache write,
because the cache is what clients read.
"""

import asyncio
import csv
import datetime as dt
import pathlib
import time

import asyncpg
import httpx
import redis.exceptions

from watchlist.modules.prices import prices_controller, prices_handler
from watchlist.modules.securities import securities_controller
from watchlist.price_service.sources.base import PriceSource
from watchlist.price_service.sources.simulated import SimulatedSource
from watchlist.price_service.sources.vendor import VendorSource
from watchlist.shared import cache, db, metrics
from watchlist.shared.logging import get_logger
from watchlist.shared.settings import get_settings
from watchlist.shared.timeutil import to_iso

logger = get_logger(__name__)

SEED_PRICES = pathlib.Path(__file__).resolve().parents[3] / "seed" / "prices.csv"
HEARTBEAT = pathlib.Path("/tmp/heartbeat")


class PriceService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._source: PriceSource | None = None
        self._ticker_to_id: dict[str, int] = {}
        self._last_published: dict[str, float] = {}
        # When each price was first seen at its current value. Both stores
        # carry this, so the cache and the fallback never disagree.
        self._effective_at: dict[str, dt.datetime] = {}
        self._last_upstream_fetch: float = 0.0
        self._upstream_prices: dict[str, float] = {}
        # Counters. Where a production system would add resilience this one
        # adds a metric; these are what are exported to Prometheus.
        self.stats = {
            "ticks": 0,
            "updates_received": 0,
            "updates_changed": 0,
            "cache_writes": 0,
            "cache_writes_rejected": 0,
            "cache_errors": 0,
            "postgres_upserts": 0,
            "postgres_errors": 0,
            "upstream_calls": 0,
            "upstream_errors": 0,
            "published": 0,
            "publish_batches": 0,
            "publish_errors": 0,
        }
        self._publisher: httpx.AsyncClient | None = None
        if self._settings.transport == "push":
            self._publisher = httpx.AsyncClient(
                base_url=self._settings.centrifugo_api_url,
                headers={"X-API-Key": self._settings.centrifugo_api_key},
                timeout=5.0,
            )

    # -- startup ----------------------------------------------------------

    async def start(self) -> None:
        # Alive from the first moment: the healthcheck reads this file, and a
        # process waiting for `make migrate` is healthy-but-unprovisioned, not
        # dead. Requiring ticks before health deadlocked `make bootstrap` on a
        # fresh volume - up --wait needs health, health needs ticks, ticks need
        # the schema, and migrate runs after up. Same lesson as the API's
        # /health: readiness must not require provisioning.
        HEARTBEAT.touch()
        await db.connect(min_size=1, max_size=4)
        await cache.register_scripts()
        await self._wait_for_schema()
        await self._reconcile_source()
        await self._sync_catalog()
        self._source = await self._build_source()
        await self._warm_cache_from_postgres()

    async def _wait_for_schema(self, timeout_seconds: float = 60.0) -> None:
        """Block until `make migrate` has run.

        The alternative is exiting, which turns an ordering detail into a
        crash loop on a cold `make up`. Waiting is also the correct restart
        behaviour: this process should tolerate its dependencies coming back
        after it does.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            async with db.pool().acquire() as conn:
                if await conn.fetchval("SELECT to_regclass('public.securities')"):
                    return
            if time.monotonic() > deadline:
                raise RuntimeError(
                    "securities table still missing after "
                    f"{timeout_seconds:.0f}s; run 'make migrate'"
                )
            logger.info("waiting for the schema; run 'make migrate'")
            HEARTBEAT.touch()
            await asyncio.sleep(2.0)

    async def _sync_catalog(self) -> None:
        """Populate the security catalog.

        Simulated mode reads the committed seed file and never calls the
        vendor: it is used for benchmarks and for offline demos, and a source
        that exists to avoid the vendor should not call it to start up.
        """
        rows: list[tuple[str, str]] = []
        if self._settings.price_source == "api":
            try:
                probe = VendorSource()
                catalog = await probe.catalog()
                await probe.aclose()
                rows = sorted(catalog.items())
                self.stats["upstream_calls"] += 1
                logger.info("catalog: %d tickers from the vendor", len(rows))
            except (httpx.HTTPError, RuntimeError, OSError) as exc:
                logger.warning("catalog: vendor unavailable (%s); falling back to seed file", exc)

        if not rows:
            rows = self._catalog_from_seed()

        async with db.pool().acquire() as conn:
            if rows:
                await securities_controller.upsert_many(conn, rows)
            # One unknown ticker fails the vendor's whole batch (400 "Invalid
            # ticker"), so the api source prices only the vendor's listings.
            vendor_only = self._settings.price_source == "api"
            priced = await securities_controller.all_tickers(
                conn, include_synthetic=not vendor_only
            )
            self._ticker_to_id = {r["ticker"]: r["id"] for r in priced}
            if vendor_only:
                total = len(await securities_controller.all_tickers(conn))
                if total > len(priced):
                    logger.warning(
                        "catalog: %d synthetic securities excluded from vendor pricing",
                        total - len(priced),
                    )
        if not self._ticker_to_id:
            raise RuntimeError("no securities in the catalog; cannot price anything")

    @staticmethod
    def _catalog_from_seed() -> list[tuple[str, str]]:
        seed = SEED_PRICES.parent / "securities.csv"
        if not seed.exists():
            return []
        with seed.open() as fh:
            rows = [(r["ticker"], r["name"]) for r in csv.DictReader(fh)]
        logger.info("catalog: %d tickers from %s", len(rows), seed.name)
        return rows

    async def _reconcile_source(self) -> None:
        """Make the stored prices consistent with the source about to write.

        Starting the vendor over simulated rows wipes them (and their cache
        keys - a cached simulated price would otherwise win the guard against
        the real one). Starting the simulator over real rows adopts them as
        the walk's starting point. See prices_handler.reconcile_source for why
        the two directions differ. Neither refuses to start.
        """
        async with db.pool().acquire() as conn:
            outcome = await prices_handler.reconcile_source(conn, self._settings.price_source)
        if outcome.action == "wiped_simulated":
            flushed = await cache.flush_prices()
            logger.warning("flushed %d cached prices along with the simulated rows", flushed)
        self.stats["source_reconciliation"] = outcome.action
        metrics.source_reconciliation.labels(outcome.action).set(1)
        metrics.price_source.labels(self._settings.price_source).set(1)

    async def _build_source(self) -> PriceSource:
        if self._settings.price_source == "api":
            return VendorSource()
        return SimulatedSource(await self._starting_prices())

    async def _starting_prices(self) -> dict[str, float]:
        """Real values to random-walk from: the database first, then the seed file."""
        async with db.pool().acquire() as conn:
            rows = await prices_controller.all_with_tickers(conn)
        if rows:
            logger.info("simulated: starting from %d prices in latest_prices", len(rows))
            return {r["ticker"]: float(r["price"]) for r in rows}

        if SEED_PRICES.exists():
            with SEED_PRICES.open() as fh:
                prices = {r["ticker"]: float(r["price"]) for r in csv.DictReader(fh)}
            logger.info("simulated: starting from %d prices in %s", len(prices), SEED_PRICES.name)
            return prices

        logger.warning(
            "simulated: no seeded prices; run 'make capture-prices'. Falling back to 100.00"
        )
        return dict.fromkeys(self._ticker_to_id, 100.00)

    async def _warm_cache_from_postgres(self) -> None:
        """After a Redis restart the cache is empty; refill it so the first
        snapshot read after recovery does not stampede Postgres."""
        async with db.pool().acquire() as conn:
            rows = await prices_controller.all_with_tickers(conn)
        warmed = 0
        for row in rows:
            payload = {
                "security_id": row["security_id"],
                "price": float(row["price"]),
                "effective_at": to_iso(row["effective_at"]),
                "source": row["source"],
            }
            if await cache.set_price_if_newer(
                row["ticker"],
                payload,
                payload["effective_at"],
                self._settings.price_cache_ttl_seconds,
            ):
                warmed += 1
            self._last_published[row["ticker"]] = float(row["price"])
            self._effective_at[row["ticker"]] = row["effective_at"]
        if warmed:
            logger.info("warmed %d prices into the cache", warmed)

    # -- the loop ---------------------------------------------------------

    async def run_forever(self) -> None:
        interval = self._settings.publish_interval_seconds
        while True:
            started = time.monotonic()
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                # Supervisory boundary: one bad tick must not kill the loop,
                # because the next tick supersedes it 5 seconds later.
                logger.exception("tick failed")
            elapsed = time.monotonic() - started
            metrics.ticks_total.inc()
            metrics.tick_duration_seconds.observe(elapsed)
            HEARTBEAT.touch()
            if elapsed > interval:
                # The one number to watch first: past this point the system is
                # no longer meeting the brief, whatever else the dashboards say.
                logger.warning("tick took %.2fs, longer than the %.1fs interval", elapsed, interval)
            await asyncio.sleep(max(0.0, interval - elapsed))

    async def tick(self) -> None:
        self.stats["ticks"] += 1
        tickers = list(self._ticker_to_id)

        prices = await self._current_prices(tickers)
        if not prices:
            return
        self.stats["updates_received"] += len(prices)
        metrics.prices_received_total.inc(len(prices))

        now = dt.datetime.now(dt.UTC)
        changed = {t: p for t, p in prices.items() if self._last_published.get(t) != p}
        for ticker in changed:
            self._effective_at[ticker] = now
        # A price seen for the first time after a restart still needs a stamp.
        for ticker in prices:
            self._effective_at.setdefault(ticker, now)
        self.stats["updates_changed"] += len(changed)
        metrics.prices_changed_total.inc(len(changed))

        # Three writes, three scopes, none blocking the others. A Postgres
        # stall must not delay the publish; a Centrifugo stall must not delay
        # the cache.
        await asyncio.gather(
            self._write_cache(prices),
            self._write_postgres(changed),
            self._publish(changed),
        )
        self._last_published.update(prices)

        if changed:
            logger.info(
                "tick %d: %d/%d changed (source=%s)",
                self.stats["ticks"],
                len(changed),
                len(prices),
                self._source.name,
            )
        else:
            logger.debug("tick %d: %d prices, none changed", self.stats["ticks"], len(prices))

    async def _current_prices(self, tickers: list[str]) -> dict[str, float]:
        """Call upstream only when its own interval has elapsed.

        The vendor is polled at UPSTREAM_POLL_INTERVAL_SECONDS, set by its cost
        and rate limit. Clients are updated every PUBLISH_INTERVAL_SECONDS from
        what was last read. Decoupling these is what lets the product promise 5s
        regardless of what the vendor allows, and it is the reason a cache sits
        between them at all.
        """
        now = time.monotonic()
        due = now - self._last_upstream_fetch >= self._settings.upstream_poll_interval_seconds
        if not due and self._upstream_prices:
            return self._upstream_prices

        try:
            self._upstream_prices = await self._source.fetch(tickers)
            self._last_upstream_fetch = now
            self.stats["upstream_calls"] += 1
            metrics.upstream_calls_total.labels("ok").inc()
        except (httpx.HTTPError, OSError, ValueError):
            self.stats["upstream_errors"] += 1
            metrics.upstream_calls_total.labels("error").inc()
            logger.exception("upstream fetch failed; serving the previous read")
        return self._upstream_prices

    async def _write_cache(self, prices: dict[str, float]) -> None:
        """Refresh every current price, not just the changed ones. One round trip."""
        entries = []
        for ticker, price in prices.items():
            iso = to_iso(self._effective_at[ticker])
            payload = {
                "security_id": self._ticker_to_id[ticker],
                "price": price,
                "effective_at": iso,
                "source": self._source.name,
            }
            entries.append((ticker, payload, iso))
        try:
            written, rejected = await cache.set_prices_if_newer(
                entries, self._settings.price_cache_ttl_seconds
            )
            self.stats["cache_writes"] += written
            self.stats["cache_writes_rejected"] += rejected
            metrics.cache_writes_total.labels("written").inc(written)
            metrics.cache_writes_total.labels("rejected").inc(rejected)
        except (redis.exceptions.RedisError, OSError):
            self.stats["cache_errors"] += 1
            metrics.cache_writes_total.labels("error").inc(len(entries))
            logger.exception("cache write failed for %d tickers", len(entries))

    async def _write_postgres(self, changed: dict[str, float]) -> None:
        """Only changed prices. Rewriting 99 unchanged rows every 5s is churn."""
        if not changed:
            return
        rows = [
            (self._ticker_to_id[t], p, self._effective_at[t], self._source.name)
            for t, p in changed.items()
        ]
        try:
            async with db.pool().acquire() as conn:
                await prices_controller.upsert_many(conn, rows)
            self.stats["postgres_upserts"] += len(rows)
            metrics.postgres_upserts_total.labels("ok").inc(len(rows))
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError):
            metrics.postgres_upserts_total.labels("error").inc(len(rows))
            # A Postgres stall must not stop the cache write or, later, the
            # publish. Clients keep seeing fresh prices; durability lags.
            # asyncpg errors are not OSErrors; catching only OSError let the
            # connection-exhaustion incident escape to the loop's catch-all
            # uncounted.
            self.stats["postgres_errors"] += 1
            logger.exception("latest_prices upsert failed for %d rows", len(rows))

    async def _publish(self, changed: dict[str, float]) -> None:
        """One publish per *changed* ticker, batched into one HTTP call.

        Per-ticker channels, never per-user: the backend publishes once per
        changed ticker per tick regardless of how many users watch it, and the
        broker fans out. Unchanged tickers cost nothing - this is the reduction
        that makes push cheap, and the thing polling structurally cannot do.
        """
        if self._publisher is None or not changed:
            return
        commands = []
        for ticker, price in changed.items():
            commands.append(
                {
                    "publish": {
                        "channel": f"ticker:{ticker}",
                        "data": {
                            "security_id": self._ticker_to_id[ticker],
                            "ticker": ticker,
                            "price": price,
                            "effective_at": to_iso(self._effective_at[ticker]),
                            "source": self._source.name,
                        },
                    }
                }
            )
        try:
            t0 = time.perf_counter()
            response = await self._publisher.post("/batch", json={"commands": commands})
            response.raise_for_status()
            metrics.publish_batch_duration_seconds.observe(time.perf_counter() - t0)
            failed = sum(1 for r in response.json().get("replies", []) if "error" in r)
            self.stats["published"] += len(commands) - failed
            self.stats["publish_errors"] += failed
            self.stats["publish_batches"] += 1
            metrics.publishes_total.labels("ok").inc(len(commands) - failed)
            metrics.publishes_total.labels("error").inc(failed)
            if failed:
                logger.warning("centrifugo rejected %d of %d publishes", failed, len(commands))
        except (httpx.HTTPError, ValueError):
            # Realtime is at-most-once by design: a dropped tick is superseded
            # by the next one in 5 seconds. Count it; do not retry it.
            self.stats["publish_errors"] += len(commands)
            metrics.publishes_total.labels("error").inc(len(commands))
            logger.exception("publish batch of %d failed", len(commands))

    async def stop(self) -> None:
        if self._publisher is not None:
            await self._publisher.aclose()
        if self._source is not None:
            await self._source.aclose()
        await cache.close()
        await db.disconnect()
