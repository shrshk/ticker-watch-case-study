-- Refresh tokens. Access tokens are short-lived JWTs verified without a database
-- read; this table is what lets them stay short. One row per issued refresh
-- token, stored hashed so a database leak does not hand out sessions.

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Set when the token is used (rotation) or explicitly revoked. A revoked
    -- token presented again is treated as theft and revokes the whole user.
    revoked_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);
