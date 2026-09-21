"""Redis client plus the price-cache key helpers.

Redis is a read-through cache in front of `latest_prices`, never the source of
truth. A miss, an expired key or an outage falls through to Postgres and still
returns a correct answer.
"""

import json

import redis.asyncio as aioredis

from watchlist.shared.settings import get_settings

_client: aioredis.Redis | None = None

# Compare-and-set on effective_at. Both price writes are guarded, not just the
# Postgres one: if only Postgres were guarded the cache could hold an older
# price than the table it caches, which makes the fallback path more correct
# than the fast path.
#
# The comparison rejects only a *strictly older* write. An equal effective_at
# is accepted so the price service can rewrite every ticker each tick and
# refresh its TTL - which is what makes a quiet ticker survive, and what
# refills the cache after a Redis restart even when no price has moved.
_CAS_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if existing then
    local ok, decoded = pcall(cjson.decode, existing)
    if ok and decoded['effective_at'] and decoded['effective_at'] > ARGV[2] then
        return 0
    end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
"""

_cas = None


def client() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def price_key(ticker: str) -> str:
    return f"price:{ticker}"


async def register_scripts() -> None:
    global _cas
    _cas = client().register_script(_CAS_SCRIPT)


async def set_price_if_newer(
    ticker: str, payload: dict, effective_at_iso: str, ttl_seconds: int
) -> bool:
    """Write the price only if it is newer than what is cached. Returns True if written."""
    if _cas is None:
        await register_scripts()
    written = await _cas(
        keys=[price_key(ticker)],
        args=[json.dumps(payload, separators=(",", ":")), effective_at_iso, ttl_seconds],
    )
    return bool(written)


async def set_prices_if_newer(
    entries: list[tuple[str, dict, str]], ttl_seconds: int
) -> tuple[int, int]:
    """Guarded write for many tickers in one round trip. Returns (written, rejected).

    98 sequential EVALSHA calls were ~30ms per tick. A pipeline is one round
    trip, and in phase 3 the same loop gains a publish per ticker.
    """
    if _cas is None:
        await register_scripts()
    if not entries:
        return 0, 0
    async with client().pipeline(transaction=False) as pipe:
        for ticker, payload, effective_at_iso in entries:
            # Awaited even though it targets the pipeline: redis-py's async
            # Script.__call__ is a coroutine that *queues* the command. Without
            # the await nothing is queued and the pipeline executes empty -
            # the regression test for this is what caught it.
            await _cas(
                keys=[price_key(ticker)],
                args=[json.dumps(payload, separators=(",", ":")), effective_at_iso, ttl_seconds],
                client=pipe,
            )
        results = await pipe.execute()
    written = sum(1 for r in results if r)
    return written, len(results) - written


async def get_prices(tickers: list[str]) -> dict[str, dict]:
    """MGET the whole watchlist in one round trip. Missing keys are simply absent."""
    if not tickers:
        return {}
    raw = await client().mget([price_key(t) for t in tickers])
    out: dict[str, dict] = {}
    for ticker, value in zip(tickers, raw, strict=True):
        if value:
            out[ticker] = json.loads(value)
    return out


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
