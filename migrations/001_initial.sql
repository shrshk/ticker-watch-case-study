-- Initial schema. There is no migration framework here on purpose: the brief
-- asks that user data survive a restart, not that the schema evolve. This file
-- is idempotent and applied by `make migrate`.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    email         TEXT,
    first_name    TEXT NOT NULL DEFAULT '',
    last_name     TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS securities (
    id           BIGSERIAL PRIMARY KEY,
    ticker       TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    exchange     TEXT,
    asset_type   TEXT,
    -- Only 99 real securities exist behind the vendor API. Benchmark seeds pad
    -- the catalog to answer the database-sizing question; this flag keeps the
    -- padding honest and greppable. Never true in a demo.
    is_synthetic BOOLEAN NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Search: exact ticker -> ticker prefix -> name match.
CREATE INDEX IF NOT EXISTS idx_securities_ticker_trgm ON securities USING gin (ticker gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_securities_name_trgm   ON securities USING gin (name   gin_trgm_ops);

CREATE TABLE IF NOT EXISTS watchlists (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL DEFAULT 'default',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_watchlists_user_name ON watchlists(user_id, name);

CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id BIGINT NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    security_id  BIGINT NOT NULL REFERENCES securities(id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (watchlist_id, security_id)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_items_security ON watchlist_items(security_id);

-- One row per security, overwritten every tick. The durable snapshot source
-- and the fallback behind the Redis cache.
CREATE TABLE IF NOT EXISTS latest_prices (
    security_id  BIGINT PRIMARY KEY REFERENCES securities(id) ON DELETE CASCADE,
    price        DOUBLE PRECISION NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    source       TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
