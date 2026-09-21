# db/

`demo.sql` is written by `make db-dump` and loaded by Postgres on a fresh
volume only (the image's `/docker-entrypoint-initdb.d/` hook). It holds the
schema plus demo state - the 99 securities, `latest_prices`, `user1`/`user2`
and their watchlists - and never load-test users or refresh tokens.

Regenerate it from a stack with no load users; never hand-edit it.
Migrations in `../migrations/` remain the source of truth for the schema.
