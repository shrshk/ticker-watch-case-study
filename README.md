# Stock Watchlist

A stock watchlist service and client for the Albert product engineering case study.
Users log in, search stocks by ticker or company name, keep a watchlist, and see
prices refresh every 5 seconds.

The original case study brief is preserved at
[`archive/ORIGINAL_README.md`](archive/ORIGINAL_README.md).

---

## Quick start

```bash
cp .env.example .env         # then paste the API key from the case study email
make bootstrap               # build, start, migrate, create demo users
make open-app                # http://localhost:3000
```

Log in as `user1` / `password` (or `user2` / `password`).

`make bootstrap` is `build` + `up-detached` + `migrate` + `createusers`. If you
prefer the original scaffold's rhythm, those targets still exist individually,
and `make up` runs the stack in the foreground. `make help` lists everything.

**Markets are closed most of the time.** Outside trading hours the vendor
returns the last close, so nothing moves on screen. To see the update path
working at any hour:

```bash
make reset-prices
make up PRICE_SOURCE=simulated
```

The UI shows a `simulated prices` badge whenever the numbers are generated, so
nobody watching a demo mistakes them for live market data.

---

## What this is, and what it is not

This is a case study, so the deliverable is a working product **plus** the
reasoning behind each choice. It runs on one laptop via Docker Compose.

**Deliberate non-goals:** multi-tenancy, high availability, failover, cloud
deployment, TLS, secrets management, exchange-grade price correctness, order
entry, and audit logging. Login is required by the brief and is built, but kept
minimal.

Where a production system would add resilience, this one usually adds a counter
and a note. A guarded write with a rejection count is in scope; a retry
framework with dead-letter handling is not.

---

## What the vendor API actually provides

Worth stating up front, because it shapes several decisions:

| | |
|---|---|
| Catalog size | **99 tickers**, fixed |
| Prices per call | **all 99 in one request** (~180ms, 1.2KB) |
| Timestamp | none — the response is `{"AAPL": 336.13}` |
| Unpriced tickers | `ATVI` returns `null` (delisted after the Microsoft acquisition) |
| Auth | `Albert-Case-Study-API-Key` header; `403` without it |

The brief says to treat this as a third-party integration with per-call pricing
and to limit the number of calls. The architecture's answer:

- **One process** (`price-service`) is the only caller. Nothing else in the
  system may reach the vendor.
- **One call per tick** covers the entire universe, because the prices endpoint
  accepts every ticker in a single query string.
- **The vendor's cadence is decoupled from the client's.** `UPSTREAM_POLL_INTERVAL_SECONDS`
  is set by cost and rate limits; `PUBLISH_INTERVAL_SECONDS` is the 5s the
  product promises. Raising the former cuts vendor spend with no change to the
  client contract.

The result: vendor call volume is **constant at one call per interval**, whether
the system has ten users or ten million. That property is the whole reason a
cache sits between the vendor and the read path.

Because the vendor supplies no timestamp, `effective_at` is our own observation
time, meaning *"when this price was first seen at this value"*. Both stores
agree on it.

---

## Architecture

```
Albert API (or the simulator)
        │   one call per tick, all 99 tickers
        ▼
  price-service ──────┬──► Redis price cache      (guarded write, every tick)
   5s publish tick    │
                      └──► Postgres latest_prices (guarded upsert, on change)

Client load / refresh
        │
      FastAPI ──► Postgres: watchlist membership
              └─► Redis ──fallback──► Postgres: current prices
```

| Component | Responsibility |
|---|---|
| **FastAPI** (`api`) | Business APIs only: login, search, watchlist CRUD, the snapshot. It does **not** own realtime fanout. |
| **Postgres** (`db`) | Durable source of truth: users, securities, watchlists, membership, `latest_prices`. Not in the update path. |
| **Redis** | Read-through cache in front of `latest_prices`. Never authoritative. |
| **price-service** | The only writer of prices anywhere, and the only caller of the vendor. |
| **React + Vite** (`client`) | Login, plus one screen: search bar and a live watchlist. |

The two Python services share one image and one dependency set but run as
separate processes with separate lifecycles. Splitting the image is a Dockerfile
change, not a redesign.

### Why three writes have three different scopes

Each tick does three things, and each has a deliberately different scope:

| Write | Scope | Why |
|---|---|---|
| Redis cache | **every** current price | Refreshes the TTL so a quiet ticker never expires, and refills the cache after a Redis restart without waiting for a price to move. |
| `latest_prices` | **changed** prices only | The durable record of a price *change*. Rewriting 99 unchanged rows every 5 seconds is pure churn. |
| Publish (phase 3) | **changed** prices only | Per change by definition — the reduction that makes push cheap. |

The cache write and the durable write run concurrently. A Postgres stall must
not stop the cache write, because the cache is what clients read.

---

## Transport: polling now, push next

A 5-second cadence invites an obvious question — why not just poll? Since the
brief's stated focus is service↔client communication, that is the central design
question, and it deserves a measurement rather than an argument.

**So polling is built first, and it is not a strawman.** `GET /watchlist`
already returns membership and prices together, so polling is a `setInterval`
on the client and no extra server work at all. It is simple, has no connection
state, scales behind any load balancer, survives every proxy, and needs no
reconnect logic. It genuinely satisfies the brief.

Both transports sit behind one hook — `useWatchlist(token)` — so the rest of the
UI cannot tell which is active. That is what keeps the eventual comparison
like-for-like.

The client jitters its first poll. A million clients on a 5s timer otherwise
drift into aligned spikes, and that is client-side behaviour a server cannot
enforce — one of the structural costs of polling, not an implementation detail.

### Where polling breaks — measured

Full tables and method in [`docs/measurements.md`](docs/measurements.md).

**Polling breaks between 25,000 and 27,500 concurrent clients** on this laptop
— about 5,000 requests per second, with the API on 4 uvicorn workers.

| clients | req/s | p50 | p99 | errors | API CPU |
|---|---|---|---|---|---|
| 15,000 | 2,871 | 2.7ms | 14.5ms | 0 | 317% |
| **25,000** | **4,782** | **7.8ms** | **72.7ms** | **0** | **383% of 400%** |
| 27,500 | 3,762 | 855ms | 19,281ms | 263 | 417% |
| 30,000 | 3,758 | 4,045ms | 21,605ms | 555 | 422% |

It is congestive collapse, not a plateau: past the knee throughput *falls*,
p50 rises 500-fold, and API memory grows from 228 MB to over 1.1 GB as requests
queue. The API exhausts its worker CPU first; Postgres is at 194% and Redis at
13% when it goes.

**Update latency is fixed by the interval, not by load.** Measured at the
client, from a price's `effective_at` to the moment a client sees it:

| clients | p50 | p99 |
|---|---|---|
| 1,000 | 2,548ms | 4,970ms |
| 25,000 | 2,573ms | 4,961ms |

Half an interval at p50, a full interval at p99, at every load level. That is
arithmetic, and no amount of server capacity improves it. This is the number
push has to beat.

**And the cost is paid whether or not anything changed.** Measured at 16,900
bytes per client per minute, constant across the whole range. At
`SIM_CHANGE_RATIO=0.30`, roughly 70% of every response is data the client
already had. Extrapolating the measured per-client cost:

| concurrent clients | required req/s | egress | stacks at 5,000 req/s |
|---|---|---|---|
| 25,000 | 5,000 | 7 MB/s | 1 |
| 1,000,000 | 200,000 | 282 MB/s | **40** |

Forty API stacks to deliver mostly-unchanged data every five seconds is the
argument for push, as a number rather than an opinion.

**What push will cost, stated honestly:** connection state, reconnect handling,
the snapshot/subscribe ordering problem, slow-consumer management, and one more
component to run. Phase 3 runs identical load under both and publishes the
comparison.

---

## Data model

```
users ──< watchlists ──< watchlist_items >── securities ──1:1── latest_prices
```

`latest_prices` is one row per security — about 100 rows, entirely in
`shared_buffers`. It is the durable snapshot source and the fallback behind the
cache.

A few choices worth naming:

- **`DOUBLE PRECISION`, not `NUMERIC`.** This is a display value refreshed every
  5 seconds, not a ledger entry. Anything an accounting system touched would use
  scaled integers instead.
- **`source` is plain `TEXT`**, unindexed. Two possible values do not justify a
  lookup table or a join on the read path. It exists to answer "where did this
  number come from".
- **`latest_prices` is keyed on `security_id` alone.** One row per security,
  overwritten each tick. A unique key including `effective_at` would permit
  several live rows per stock and break the `ON CONFLICT` guard.
- **`securities.is_synthetic`** exists because only 99 real securities exist.
  Phase 2 benchmark seeds pad the catalog to answer the database-sizing
  question; the flag keeps the padding greppable and it is never true in a demo.

### Migrations

Plain idempotent SQL in `migrations/`, applied in filename order by
`tools/migrate.py` (`make migrate`). No Alembic, no Django migrations.

There is exactly one schema and it does not evolve during the case study.
Alembic's value is chain management across deployed environments; here there is
one environment, built from scratch. Standing up a migration framework for a
single revision would be ceremony. Everything is `IF NOT EXISTS`, so `make
migrate` is safe to re-run.

---

## The read path

Redis first, `latest_prices` behind it. Postgres is authoritative; Redis is a
read-through cache in front of it. A miss, an expired key, or a Redis outage
falls through and still returns a correct answer — **never a null**. Drift
self-heals on the next tick.

`GET /watchlist` returns membership and prices together. Two round trips on app
load is a worse story than one, and it makes the phase-3 subscribe-ordering rule
simpler to implement.

The response reports which path served it and the cache hit/miss split, which is
also what the status bar in the UI shows. Verified by stopping Redis mid-session:

```
redis DOWN  -> read_path: postgres (redis unavailable) | prices intact, none null
redis BACK  -> read_path: redis | refilled within one tick, no price change needed
```

### Making the caching decision measurable

```
LATEST_PRICE_SOURCE=redis|postgres
```

`postgres` bypasses the cache entirely. This exists so the caching decision can
be measured under a burst rather than asserted. No numbers are published here
yet — that is phase 2.

---

## Out-of-order writes

Two workers, or a retry after a partial failure, can write an older price after
a newer one. Neither store detects this on its own, so **both** writes are
guarded on `effective_at`:

- **Postgres** — `WHERE excluded.effective_at > latest_prices.effective_at` on
  the upsert.
- **Redis** — a Lua compare-and-set that reads the stored `effective_at` and
  rejects anything strictly older.

Guarding Postgres alone would be worse than guarding neither: the cache could
then hold an older price than the table it caches, making the fallback path more
correct than the fast path.

The Redis guard rejects only *strictly older* writes. An equal timestamp is
accepted, which is what lets the service rewrite every ticker each tick to
refresh TTLs.

Covered by `tests/test_write_ordering.py`.

---

## Switching price sources

Simulated prices carry `now()`, so they always win the `effective_at` guard. The
reverse does not hold: switching back to the vendor outside market hours returns
the last close, whose timestamp is *older*, so every upsert would be silently
rejected while the app reported `PRICE_SOURCE=api` and kept serving generated
numbers.

Prices are fully derived state, so the fix is to clear them:

```bash
make reset-prices      # DELETE FROM latest_prices, plus FLUSH price:*
```

`price-service` **refuses to start** if the configured source does not match
what is already in `latest_prices`, and says which target to run. Without that
check the failure is silent, which is the worst mode for something that quietly
changes which numbers you are reporting.

The check runs *before* the catalog sync, so a misconfiguration never costs a
call to a vendor that charges per request.

---

## The simulated source

`SOURCE=simulated` is a random walk over **real** starting prices, read from
`seed/prices.csv`:

```
next = last * (1 + gauss(0, SIM_VOLATILITY))
```

So NVDA stays in the hundreds and a penny stock stays in cents — screenshots
look plausible and log lines stay readable.

`seed/securities.csv` and `seed/prices.csv` are captured once by
`make capture-prices` and committed. They are not a runtime dependency and not a
backup. They exist so benchmarks are reproducible from a file in git rather than
from whatever the market did that day, and so the stack boots offline.

Both source adapters are required, for three structural reasons:

- **Rate limits and cost.** Load scenarios need far more price movement than a
  per-call-priced vendor will serve.
- **Markets are closed most of the time.** A weekend demo under `SOURCE=api`
  shows nothing updating — the one thing a reviewer needs to see.
- **Controlled comparison.** The phase-3 transport comparison is only valid if
  both halves see identical price movement. `SIM_SEED` makes runs reproducible.

`SIM_CHANGE_RATIO` matters more than it looks: it is the difference between
publishing 99 updates per tick and publishing 30, and it is the main lever on
egress in every load scenario. Report it alongside any benchmark.

Simulated mode reads the catalog from the seed file and never calls the vendor —
a source that exists to avoid the vendor should not call it to start up.

---

## Auth

Username and password, bcrypt-hashed, exchanged for an HS256 JWT.

The scaffold shipped "a login method with no authentication" — it took a
username and returned the user. Strictly, the brief only says the client must
*support login*, so the scaffold satisfies it literally. A password check was
added anyway because the system's entire premise is per-user watchlists, and
because phase 3 needs a signed token regardless: Centrifugo validates a
connection token, and the cleanest source is the same secret that signs the
login token, making `POST /realtime/token` a claims transform rather than a
second auth system.

Login returns the same error whether the username is unknown or the password is
wrong, so the endpoint does not enumerate users.

---

## API

| Method | Path | |
|---|---|---|
| `POST` | `/auth/register` | Creates the user and their default watchlist |
| `POST` | `/auth/login` | Returns a JWT |
| `GET` | `/auth/me` | |
| `GET` | `/securities/search?q=` | Exact ticker → ticker prefix → name |
| `GET` | `/watchlist` | Membership **and** current prices, in one call |
| `POST` | `/watchlist/items` | `{"security_id": 72}` |
| `DELETE` | `/watchlist/items/{security_id}` | |
| `GET` | `/health` | Datastore checks; used to refuse load runs against a half-started stack |

Interactive docs at http://localhost:8000/docs (`make open-api`).

`/health` reports the price source by reading `latest_prices`, not by reporting
its own environment variable. `PRICE_SOURCE` belongs to `price-service`; the API
only ever had a copy, and a copy goes stale the moment the two are configured
apart. The client's `simulated prices` badge is derived the same way, from the
`source` on each row.

### Search

Postgres only, with `pg_trgm` indexes. Ranked exact ticker → ticker prefix →
name prefix → fuzzy name, so `NV` matches NVDA, NVAX and NVR while `NVDA` ranks
itself first. Real symbols matter here; invented tickers would not exercise it.
No Elasticsearch unless a measured requirement justifies one.

---

## Configuration

Everything is in `.env` (see `.env.example`). The ones that change behaviour:

| Variable | Default | |
|---|---|---|
| `ALBERT_API_KEY` | — | Required for `PRICE_SOURCE=api` |
| `PRICE_SOURCE` | `api` | `api` or `simulated` |
| `PUBLISH_INTERVAL_SECONDS` | `5` | What the product promises |
| `UPSTREAM_POLL_INTERVAL_SECONDS` | `5` | What the vendor costs |
| `PRICE_CACHE_TTL_SECONDS` | `60` | Must exceed the worst gap between updates |
| `LATEST_PRICE_SOURCE` | `redis` | `postgres` bypasses the cache |
| `TRANSPORT` | `poll` | `push` arrives in phase 3 |
| `SIM_VOLATILITY` / `SIM_CHANGE_RATIO` / `SIM_SEED` | `0.002` / `0.30` / `1` | Report these with any benchmark |
| `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` | `55432` / `56379` | Non-default so the stack can run beside another local Postgres or Redis |

---

## Load testing and seeding

```bash
make seed-small | seed-medium | seed-million   # 10k / 100k / 1M users
make db-bench                                  # server-side read latency
make load           CLIENTS=25000 DURATION=60s # generator on the host
make load-container CLIENTS=25000 DURATION=60s # generator inside the network
```

The load generator is a separate Go program under `tools/load_generator/`. It
is not part of the product, shares no code with the services, builds separately
and runs only under the `load` Compose profile. It lives in this repo because
the measurements are the deliverable: a reviewer checking these numbers should
not have to clone a second repo, and a separate repo drifts from the API
contract the first time an endpoint changes.

It refuses to run against a stack that is not healthy, and every result file
records the API command, worker count and which generator produced it — a
benchmark that does not state its server configuration is a number without a
meaning.

Logical users and real connections are independent parameters. Logical users
exercise database size, watchlist distribution and popularity skew; real
connections exercise sockets, memory and fanout.

**Two environment caveats that change the numbers**, both in
[`docs/measurements.md`](docs/measurements.md):

- The host has only 16,384 ephemeral ports, which caps a host-run generator at
  roughly 16,000 clients — below where the application actually breaks. Use
  `make load-container` for anything larger.
- Docker Desktop's host port forwarding adds about 85ms at p99. Same throughput,
  much worse tail. The container-generated numbers are the honest ones.

## Tests

```bash
make up-detached && make migrate   # the integration tests need the stack
make test
```

34 tests. They run against a separate `watchlist_test` database created and
dropped per run, so a test run never touches the demo data. They cover the
claims this README makes rather than the code's surface area:

| File | Proves |
|---|---|
| `test_write_ordering.py` | Both guards reject an older replay; an equal timestamp still refreshes the TTL |
| `test_read_path.py` | Cache hit, miss-fallthrough, partial hit, the `LATEST_PRICE_SOURCE` toggle, and that a price is never invented for an unpriced security |
| `test_auth.py` | Password round-trip, a malformed hash is a failed login not a crash, forged and expired tokens are rejected, registration creates a default watchlist |
| `test_search.py` | `NV` → NVDA/NVAX/NVR, exact-ticker ranking, name matching, case insensitivity |
| `test_simulated_source.py` | Prices stay near real values, the seed reproduces a walk, a price never reaches zero, the change ratio is honoured |

Lint and format with `make lint` / `make format` (ruff, line length 100).

---

## Deliberate cuts

Saying why something was not built is more useful than silently omitting it.

- **Price history.** An earlier design had a partitioned `price_history` table
  with retention management. The brief asks only that *user data* persist and
  that users see *current* prices; nothing requires a time series. It would add
  a partitioning and retention story that answers no question the case study
  asks. The extension is an append-only partitioned table alongside
  `latest_prices`, and it would enable charts, portfolio value history,
  backtesting and analytics.
- **An identity provider.** Keycloak or any OIDC provider is where auth goes in
  production, replacing the `users` table, the login endpoint, and the HS256
  shared secret with JWKS. It was left out because it is a ~700MB container with
  a realm import to bootstrap, and `make up` has to stay fast on a stranger's
  laptop. It also answers no question the brief asks.
- **Market-session behaviour** (equity hours, pre/post market, crypto
  always-on). Interesting product logic; it dilutes the scaling question.
- **Kafka, Celery, Temporal, Elasticsearch, Kubernetes.** None is introduced
  until a measurement exposes a concrete need for it.

---

## What comes next

Phase 1 — the working product on polling — is complete and satisfies the brief.
The remaining phases exist to answer whether the design holds at the stated
scale.

**Phase 2 — find the limit. Done.** 1M users and 9.7M watchlist rows seeded;
database, load generator and the polling ceiling all measured. Results in
[`docs/measurements.md`](docs/measurements.md); the headlines are in the
transport section above.

**Phase 3 — push, and prove it was worth it.** Centrifugo on a `push` profile
with per-ticker `ticker:<TICKER>` channels, subscribe-buffer-snapshot-drain
ordering on connect and reconnect, then identical load under both transports
with the comparison table published here. Per-ticker channels rather than
per-user is the choice that makes the hot-ticker problem mostly disappear: one
publish, one broadcast, regardless of how many users watch a stock.

**Phase 4 — observability.** Prometheus and Grafana, with `tick_duration_seconds`
as the first number to watch: when a 5s tick takes longer than 5s, the system is
no longer meeting the brief whatever else the dashboards say.

No performance numbers will appear in this README until they have actually been
measured on this machine.

---

## Notes and caveats

- **Docker Desktop interposes a VM and a network layer.** Any connection ceiling
  found locally is not a product ceiling. Phase 2 will state the host limits it
  ran under.
- **`ATVI` has no price.** It is in the vendor catalog and returns `null`. It is
  omitted rather than given an invented number, and the client renders a dash.
- **`archive/`** holds the original Django + React scaffold, the original
  Makefile and compose file, and the original brief. The case study permits an
  entirely separate tech stack; this one uses FastAPI, uv and asyncpg. The
  Makefile keeps the scaffold's target names (`build`, `up`, `migrate`,
  `createusers`, `open-app`, `submit`) so the documented workflow still works.
- **`seed/prices.csv` was captured 2026-09-21.** Re-run `make capture-prices` to
  refresh it.
