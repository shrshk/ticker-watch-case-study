"""FastAPI app: business APIs only.

It does not own realtime fanout. In push it gains POST /realtime/token and
Centrifugo takes the client connections.
"""

import time
from contextlib import asynccontextmanager

import asyncpg
import redis.exceptions
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from watchlist.api.routers import auth, realtime, securities, watchlist
from watchlist.modules.prices import prices_controller
from watchlist.shared import cache, db, metrics
from watchlist.shared.logging import configure_logging
from watchlist.shared.settings import get_settings

logger = configure_logging("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    await cache.register_scripts()
    logger.info("api ready")
    yield
    await cache.close()
    await db.disconnect()


app = FastAPI(title="Stock Watchlist API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def record_request_metrics(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    # The route *template*, not the path: /watchlist/items/{security_id}, so
    # the label set stays small however many securities exist.
    route = request.scope.get("route")
    template = getattr(route, "path", request.url.path)
    if template != "/metrics":
        metrics.http_requests_total.labels(
            template, request.method, f"{response.status_code // 100}xx"
        ).inc()
        metrics.http_request_duration_seconds.labels(template).observe(
            time.perf_counter() - started
        )
    return response


@app.get("/metrics", tags=["ops"], include_in_schema=False)
async def prometheus_metrics() -> Response:
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


app.include_router(auth.router)
app.include_router(securities.router)
app.include_router(watchlist.router)
app.include_router(realtime.router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Is this container able to serve requests?

    Deliberately not "is the application fully provisioned". The schema is
    created by `make migrate`, which runs *after* the stack is up, so making
    health depend on it would deadlock `docker compose up --wait` on a cold
    start. Schema state is reported, not asserted.

    Used by the load targets to refuse to run against a half-started
    stack, where the numbers look real and are not.
    """
    settings = get_settings()
    checks: dict[str, str] = {}
    schema_ready = False
    sources: list[str] = []

    try:
        async with db.pool().acquire() as conn:
            await conn.fetchval("SELECT 1")
            checks["postgres"] = "ok"
            schema_ready = bool(await conn.fetchval("SELECT to_regclass('public.latest_prices')"))
            if schema_ready:
                # Report the source from the data, not from this process's
                # environment. PRICE_SOURCE belongs to the price service; the
                # API only ever had a copy, and a copy goes stale the moment
                # the two are configured apart.
                sources = await prices_controller.distinct_sources(conn)
    except (OSError, asyncpg.PostgresError, RuntimeError) as exc:
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await cache.client().ping()
        checks["redis"] = "ok"
    except (OSError, redis.exceptions.RedisError) as exc:
        checks["redis"] = f"error: {type(exc).__name__}"

    return {
        "status": "ok" if all(v == "ok" for v in checks.values()) else "degraded",
        "checks": checks,
        "schema": "ready" if schema_ready else "missing (run 'make migrate')",
        "transport": settings.transport,
        "latest_price_source": settings.latest_price_source,
        "price_sources_in_data": sources,
    }
