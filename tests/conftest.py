"""Integration tests run against the Compose stack.

They use a separate database so a test run never clobbers the demo data. Start
the stack with `make up-detached && make migrate` first; the fixtures skip the
whole suite if it is not reachable.

Note the fixture shapes: the database is created and dropped by a *synchronous*
session fixture that drives its own event loop, and every async fixture is
function-scoped. A session-scoped async fixture would bind its connections to
one event loop while each test runs in another, which surfaces as
`RuntimeError: Task attached to a different loop` rather than anything that
points at the cause.
"""

import asyncio
import os
import pathlib

import asyncpg
import pytest
import pytest_asyncio
import redis.asyncio as aioredis

ROOT = pathlib.Path(__file__).resolve().parents[1]

PG_HOST_PORT = os.getenv("POSTGRES_HOST_PORT", "55432")
REDIS_HOST_PORT = os.getenv("REDIS_HOST_PORT", "56379")

ADMIN_DSN = f"postgresql://postgres:postgres@localhost:{PG_HOST_PORT}/postgres"
TEST_DSN = f"postgresql://postgres:postgres@localhost:{PG_HOST_PORT}/watchlist_test"
TEST_REDIS_URL = f"redis://localhost:{REDIS_HOST_PORT}/15"

# Point the application's settings at the test stores before anything imports
# them; get_settings() is cached for the life of the process.
os.environ["POSTGRES_DSN"] = TEST_DSN
os.environ["REDIS_URL"] = TEST_REDIS_URL


async def _create_test_database() -> None:
    admin = await asyncio.wait_for(asyncpg.connect(dsn=ADMIN_DSN), timeout=5)
    try:
        await admin.execute("DROP DATABASE IF EXISTS watchlist_test WITH (FORCE)")
        await admin.execute("CREATE DATABASE watchlist_test")
    finally:
        await admin.close()

    conn = await asyncpg.connect(dsn=TEST_DSN)
    try:
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            await conn.execute(path.read_text())
    finally:
        await conn.close()


async def _drop_test_database() -> None:
    admin = await asyncpg.connect(dsn=ADMIN_DSN)
    try:
        await admin.execute("DROP DATABASE IF EXISTS watchlist_test WITH (FORCE)")
    finally:
        await admin.close()


@pytest.fixture(scope="session", autouse=True)
def test_database() -> None:
    """Create the schema once per run. Synchronous on purpose - see the module docstring."""
    try:
        asyncio.run(_create_test_database())
    except (TimeoutError, OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"no Postgres on localhost:{PG_HOST_PORT}; run 'make up-detached' ({exc})")
    yield
    asyncio.run(_drop_test_database())


@pytest_asyncio.fixture
async def conn(test_database) -> asyncpg.Connection:
    """A connection whose work is rolled back at the end of the test."""
    connection = await asyncpg.connect(dsn=TEST_DSN)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def redis_client() -> aioredis.Redis:
    """A clean Redis database, with the cache module's cached client reset.

    The module memoises both its client and its registered Lua script. Left
    alone they would outlive the event loop that created them.
    """
    from watchlist.shared import cache

    # Drop the previous test's memoised client without awaiting close() on it:
    # its connections belong to an event loop that is already closed.
    cache._client = None  # noqa: SLF001
    cache._cas = None  # noqa: SLF001

    client = aioredis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except (OSError, aioredis.RedisError) as exc:
        await client.aclose()
        pytest.skip(f"no Redis on localhost:{REDIS_HOST_PORT}; run 'make up-detached' ({exc})")

    await client.flushdb()
    cache._client = client  # noqa: SLF001 - share one client with the code under test
    await cache.register_scripts()

    yield client

    await client.flushdb()
    await client.aclose()
    cache._client = None  # noqa: SLF001
    cache._cas = None  # noqa: SLF001
