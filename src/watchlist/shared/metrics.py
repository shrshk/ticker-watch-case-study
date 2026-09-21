"""Prometheus metrics for both services.

Every number the documentation quotes should be re-derivable from a live
stack rather than trusted. This module owns the registry; each service
records into it and exposes it at /metrics in Prometheus text format.

Labels are low-cardinality on purpose: a route template, a status class, a
read path. Never a user id, never a ticker - the hot-ticker measurement uses
Centrifugo's own histogram for that, not a per-ticker label here.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

# -- API ------------------------------------------------------------------------

http_requests_total = Counter(
    "watchlist_http_requests_total",
    "HTTP requests handled by the API",
    ["route", "method", "status"],
)
http_request_duration_seconds = Histogram(
    "watchlist_http_request_duration_seconds",
    "HTTP request latency by route",
    ["route"],
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
snapshot_reads_total = Counter(
    "watchlist_snapshot_reads_total",
    "GET /watchlist snapshot reads by the path that served the prices",
    ["read_path"],
)
snapshot_cache_lookups_total = Counter(
    "watchlist_snapshot_cache_lookups_total",
    "Individual price lookups in the snapshot path, hit or miss",
    ["result"],
)

# -- price service ------------------------------------------------------------------

ticks_total = Counter("watchlist_price_ticks_total", "Publish ticks completed")
tick_duration_seconds = Histogram(
    "watchlist_price_tick_duration_seconds",
    "Wall time of one publish tick; must stay well under the publish interval",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
upstream_calls_total = Counter(
    "watchlist_price_upstream_calls_total", "Calls to the vendor price API", ["result"]
)
prices_received_total = Counter(
    "watchlist_prices_received_total", "Prices read from the source, all tickers"
)
prices_changed_total = Counter(
    "watchlist_prices_changed_total", "Prices that differed from the last published value"
)
cache_writes_total = Counter(
    "watchlist_price_cache_writes_total",
    "Guarded Redis writes",
    ["result"],  # written | rejected | error
)
postgres_upserts_total = Counter(
    "watchlist_latest_prices_upserts_total",
    "Guarded latest_prices upserts",
    ["result"],  # ok | error
)
publishes_total = Counter(
    "watchlist_publishes_total",
    "Per-ticker publications sent to Centrifugo",
    ["result"],  # ok | error
)
publish_batch_duration_seconds = Histogram(
    "watchlist_publish_batch_duration_seconds",
    "Latency of one /api/batch call to Centrifugo",
    buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1),
)
price_source = Gauge(
    "watchlist_price_source_info", "Active price source (label carries the value)", ["source"]
)
source_reconciliation = Gauge(
    "watchlist_source_reconciliation_info",
    "What starting the price source had to do to the stored rows",
    ["action"],
)


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
