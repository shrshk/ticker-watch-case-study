"""Both services expose Prometheus metrics; the names the docs quote exist."""

from watchlist.shared import metrics

EXPECTED = [
    "watchlist_http_requests_total",
    "watchlist_http_request_duration_seconds",
    "watchlist_snapshot_reads_total",
    "watchlist_snapshot_cache_lookups_total",
    "watchlist_price_ticks_total",
    "watchlist_price_tick_duration_seconds",
    "watchlist_price_upstream_calls_total",
    "watchlist_prices_changed_total",
    "watchlist_price_cache_writes_total",
    "watchlist_latest_prices_upserts_total",
    "watchlist_publishes_total",
]


def test_every_documented_metric_is_registered():
    metrics.ticks_total.inc(0)
    metrics.publishes_total.labels("ok").inc(0)
    metrics.cache_writes_total.labels("written").inc(0)
    metrics.upstream_calls_total.labels("ok").inc(0)
    metrics.postgres_upserts_total.labels("ok").inc(0)
    metrics.snapshot_reads_total.labels("redis").inc(0)
    metrics.snapshot_cache_lookups_total.labels("hit").inc(0)
    metrics.http_requests_total.labels("/watchlist", "GET", "2xx").inc(0)
    metrics.http_request_duration_seconds.labels("/watchlist").observe(0)
    metrics.prices_changed_total.inc(0)
    body, content_type = metrics.render()
    text = body.decode()
    assert content_type.startswith("text/plain")
    missing = [name for name in EXPECTED if name not in text]
    assert not missing, f"metrics missing from /metrics output: {missing}"


def test_route_labels_are_templates_not_paths():
    """A per-security path in a label would be unbounded cardinality."""
    metrics.http_requests_total.labels("/watchlist/items/{security_id}", "DELETE", "2xx").inc()
    text = metrics.render()[0].decode()
    assert 'route="/watchlist/items/{security_id}"' in text
