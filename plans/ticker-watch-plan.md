# Stock Watchlist Service — Implementation Plan

## 0. The Brief

Build a stock watchlist service and client application:

* users search stocks by name or ticker and add them to a watchlist
* users remove stocks from their watchlist
* users see current prices for stocks in their watchlist
* **prices update every 5 seconds**
* the service is designed to support millions of users, each with their own watchlist
* user data is persisted in a database and reused across restarts
* **the focus is service architecture and service↔client communication**
* the client supports login and one screen containing a search bar and a list of stocks with current prices; it does not need to be polished

Everything below serves those requirements. Where this plan goes beyond them it says so explicitly.

---

## 1. Framing — Case Study, Not Production

The deliverable is an instrumented experiment that runs on one laptop via Docker Compose, not a production system. Read this before building.

**Explicit non-goals.** No multi-tenancy, high availability, failover, cloud deployment, TLS or secrets management, exchange-grade price correctness, order entry, audit logging, or migration/rollback beyond initial schema creation. Login is required by the brief and is built, but kept minimal (§8).

**Scope rule.** Where a production system would add resilience, this one adds a metric and a README note. A bounded buffer with a `dropped_total` counter is in scope; a retry framework with dead-letter handling is not. When the choice is between handling a failure and measuring it, measure it.

**Single machine.** One Compose stack, one host. Horizontal scaling is discussed in the README, not implemented.

The test for any addition: does it help answer one of the questions in §22? If not, leave it out even if a real system would need it.

Follow: build → measure → identify bottleneck → optimize → measure again. The strongest outcome is explaining why each component exists and what the measured behavior was — not the number of technologies used.

---

## 2. Technology Stack

**Core**

* Python 3.12+ / FastAPI
* PostgreSQL 16
* Redis 7
* Centrifugo
* React + Vite (client)
* Docker Compose

**Optional (Compose profiles, never required for startup)**

* NATS — swappable broker for the scale experiment (§11)
* Real price API integration (`SOURCE=api`) plus an API key
* Load generator (Go)
* Prometheus + Grafana

---

## 3. Responsibilities by Component

**FastAPI** — business APIs only: login/token issuance, stock search, watchlist CRUD, watchlist snapshot with current prices, Centrifugo connection token. It does **not** own realtime fanout.

**PostgreSQL** — durable source of truth for users, securities, watchlists, watchlist membership, and `latest_prices`. It is **not** in the realtime fanout path.

**Redis** — two jobs, deliberately: (1) read-through cache for the price snapshot path, (2) Centrifugo's engine, providing inter-node pub/sub, channel history and presence. That both jobs land on one single-threaded event loop is the central thing this case study measures (§11).

**Centrifugo** — all client-facing realtime: WebSocket/SSE connections, channel subscriptions, reconnect handling, per-channel fanout, horizontal scaling.

**React client** — login screen plus one main screen: search bar, watchlist, live prices.

---

## 4. Architecture

```
Price source (vendor API or simulated)
        |
        v
    price-service              (5s publish tick)
        |
        +--> Redis price cache        (guarded write)
        |
        +--> Postgres latest_prices   (guarded upsert)
        |
        +--> Centrifugo publish API --> Redis engine --> browser clients
```

Snapshot path, separate from realtime:

```
Client loads watchlist
        |
       FastAPI
        +-- Postgres: watchlist membership
        +-- Redis -> fallback Postgres: current prices
```

A client must never wait for a realtime event to learn a current price.

**The price service never talks to the broker directly.** It publishes through Centrifugo's HTTP publish API. Centrifugo's engine handles the broker, which is what makes the Redis↔NATS swap in §11 a config change with zero application code affected.

---

## 5. Transport — Build Polling First, Then Prove Push

A 5-second cadence invites the obvious question: why not just poll? The brief's focus is service↔client communication, so this is the central design question — and the answer should be demonstrated, not argued.

**Build polling first.** `GET /watchlist` already returns membership and prices, so polling mode is a `setInterval` on the client and no server work at all. It genuinely satisfies the brief: simple, no connection state, scales behind any load balancer, survives every proxy, no reconnect logic. Ship it, then measure it.

**Then make the transport a toggle.**

```
TRANSPORT=poll|push
```

Same pattern as `LATEST_PRICE_SOURCE` (§9). Run identical load under both and put the table in the README.

**What the measurement should show.**

* *Polling cost is fixed per user per interval, regardless of change.* 100k concurrent clients at 10 tickers each is 20k req/s — each request paying token validation, a watchlist lookup and an `MGET` — every 5 seconds, forever, whether or not a single price moved. Push does one broadcast per changed ticker per tick and the broker fans out. Expect FastAPI, not Redis, to be the wall.
* *Unchanged tickers cost nothing under push.* Polling re-transfers the full watchlist every cycle.
* *Polling clients synchronize.* A million clients on a 5s timer drift into aligned spikes unless every client jitters correctly, which is client-side behavior you cannot enforce.
* *5 seconds is a product decision, not an architectural ceiling.* Push makes sub-second a config change; polling makes it a rewrite, with request rate scaling linearly as the interval shrinks.

**What push costs, stated honestly.** Connection state, reconnect handling, the snapshot/subscribe ordering problem (§10), slow-consumer management (§13), and one more component to run. Those are real, and they are the reason Centrifugo exists here rather than hand-rolled WebSockets.

**Why the order matters.** Starting with polling means the push architecture is introduced as a response to a measured limit rather than assumed from the start. If an interviewer asks whether this is over-engineered, the answer is a number and a working polling implementation — not a defense.

---

## 6. Delivery Semantics

Three paths, three guarantees. State them explicitly.

**Realtime path — ephemeral by design.** At-most-once. A dropped update is acceptable because another arrives within 5 seconds and supersedes it. No replay requirement.

**Snapshot path — must be correct.** This is what the user sees on load and after every reconnect. Redis with Postgres fallback, never a null (§9).

**Durable path — user data only.** Users, watchlists, membership, and `latest_prices`. The brief requires user data to survive restart; it does not ask for price history (see §23 for why that was cut).

---

## 7. Service Boundaries

### api-service (FastAPI)

```
POST   /auth/login                     -> JWT
GET    /auth/me
GET    /securities/search?q=nv
GET    /watchlist                      -> membership + current prices (one call)
POST   /watchlist/items                -> {security_id}
DELETE /watchlist/items/{security_id}
POST   /realtime/token                 -> Centrifugo connection token
```

`GET /watchlist` returns membership and prices together. Two round trips on app load is a worse story than one, and it makes the §10 ordering rule simpler to implement.

### price-service

Owns prices end to end. One tick loop, one set of writes, a **swappable source adapter**:

```
SOURCE=api          # real upstream provider + API key
SOURCE=simulated    # generated prices
```

On each 5-second publish tick:

1. read current prices from the source adapter
2. normalize to the internal price event
3. write Redis cache, guarded on `effective_at` (§9)
4. upsert `latest_prices`, guarded on `effective_at` (§8)
5. publish changed tickers via the Centrifugo publish API, batched

Only tickers whose price actually changed are published. Steps 3–5 must not block each other; a Postgres stall must not stop publishing (§21).

The price service is the only writer to prices anywhere in the system. There is no external mutation path, so cache invalidation is not a problem to solve — but out-of-order writes are (§9).

**Upstream poll interval is not the client cadence.** The vendor is polled at `UPSTREAM_POLL_INTERVAL`, set by its rate limit; clients are updated every 5 seconds from cache. Decoupling these is what lets the product promise 5s regardless of what the vendor allows, and it is the reason a cache sits between them at all.

**~~Poll the union of watchlisted tickers, not the whole catalog.~~** *Overtaken (2026-09-21): the vendor exposes 99 tickers and returns all of them in one call (~180ms, 1.2 KB). One call per tick covers the universe for any number of users, so there is nothing to narrow. The union query was never built.*

#### Why both source adapters are required

`SOURCE=api` is the real integration and proves the interface is not a toy. It is the default for demos during market hours. But it cannot drive the load scenarios, for three structural reasons worth stating in the README:

* **Rate limits and cost.** The vendor prices per call. One call per tick is the floor, and simulated mode is what lets a benchmark run for an hour without paying for 720 of them. *(Corrected from "10k tickers": the catalog is 99.)*
* **Markets are closed most of the time.** The vendor endpoint responds around the clock, but equities return the last close outside trading hours, so prices sit frozen. A demo on a weekend or at 9pm under `SOURCE=api` shows nothing updating — the one thing a reviewer needs to see. This is the reason simulated mode exists even for non-benchmark use.
* **Controlled comparison.** Scenario B is only valid if both transports see identical price movement. Live market data varies per run, including the change ratio, which is the single biggest driver of publish volume.

#### Simulated mode

Because real starting prices are captured at seed time (§16), simulated mode is a random walk over seeded values, not invented numbers:

```
next = last * (1 + gauss(0, VOLATILITY))
```

with `last` initialised from `seed/prices.csv`. That keeps every ticker near its real value — NVDA in the hundreds, a penny stock in cents — so screenshots look plausible and log lines are readable. The whole implementation is a few lines.

Parameters, all reported alongside any benchmark rather than left as incidental defaults:

```
SIM_VOLATILITY        # per-tick stddev
SIM_CHANGE_RATIO      # share of tickers that move each tick
~~SIM_TICKER_COUNT~~        # never built: the catalog is fixed at the vendor's 99
SIM_SEED              # RNG seed
```

`SIM_CHANGE_RATIO` matters more than it looks: it is the difference between publishing 10k updates per tick and publishing 400, and it is the main lever on egress in every scenario.

`SIM_SEED` makes runs reproducible. Without it, Scenario B's two halves see different price movement and the comparison is not controlled — the same reason the `bench-*` targets force `SOURCE=simulated` (§24).

### centrifugo

Separate container, `push` profile only. Maintains client connections, exposes `ticker:<TICKER>` channels, validates JWTs issued by FastAPI. Engine is configured, not coded (§11).

### client (React + Vite)

See §14.

### load-generator (Go) — in repo, outside the default stack

Not part of the product. It shares no code with the services, builds separately, and runs only under the `load` profile with explicit arguments. It lives in this repo anyway because the measurements *are* the deliverable: a reviewer checking the README's numbers should not have to clone a second repo, and a separate repo drifts from the API contract the first time an endpoint changes.

It must verify the main stack is up and healthy before generating any load, and fail with a clear message otherwise — a run against a half-started stack produces numbers that look real and are not.

Logical users and real connections are **independent parameters**:

```
LOGICAL_USERS=1000000
CLIENTS=20000
```

Under `TRANSPORT=poll` it drives HTTP clients on jittered 5s timers. Under `TRANSPORT=push` it opens real Centrifugo connections and subscribes them. Both modes record update latency measured at the client, plus success and failure counts; push mode additionally simulates reconnect storms and slow consumers.

---

## 8. Data Model

```sql
CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE securities (
    id         BIGSERIAL PRIMARY KEY,
    ticker     TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    exchange   TEXT,
    asset_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE watchlists (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id),
    name       TEXT NOT NULL DEFAULT 'default',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE watchlist_items (
    watchlist_id BIGINT NOT NULL REFERENCES watchlists(id),
    security_id  BIGINT NOT NULL REFERENCES securities(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (watchlist_id, security_id)
);

CREATE INDEX idx_watchlist_items_watchlist ON watchlist_items(watchlist_id);
CREATE INDEX idx_watchlist_items_security  ON watchlist_items(security_id);

CREATE TABLE latest_prices (
    security_id  BIGINT PRIMARY KEY REFERENCES securities(id),
    price        DOUBLE PRECISION NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    source       TEXT NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`latest_prices` is one row per security — ~10k rows, entirely in `shared_buffers`. It is the durable snapshot source and the fallback behind the Redis cache. Upsert every tick, guarded on ordering:

```sql
INSERT INTO latest_prices (security_id, price, effective_at, source)
VALUES (...)
ON CONFLICT (security_id) DO UPDATE
SET price = excluded.price,
    effective_at = excluded.effective_at,
    source = excluded.source,
    updated_at = now()
WHERE excluded.effective_at > latest_prices.effective_at;
```

**Price type.** `DOUBLE PRECISION`, not `NUMERIC`. `NUMERIC` is correct for money at rest, but this is a display value refreshed every 5 seconds, not a ledger entry. Note in the README that anything an accounting system touches would use scaled integers instead — knowing the distinction is the point.

**The `source` column.** Plain TEXT — `'api'` or `'simulated'`. No lookup table and no foreign key: two values do not justify a join on the snapshot read path, and the column exists to answer "where did this number come from," not to be normalised. Leave it unindexed; at 10k rows a sequential scan beats the index.

`latest_prices` stays keyed on `security_id` alone. One row per security, overwritten each tick. A unique on `(security_id, effective_at, source)` would permit several live rows per stock, which reintroduces the `DISTINCT ON` problem that got price history cut (§23) and breaks the `ON CONFLICT (security_id)` guard above.

**Switching sources wipes prices.** Simulated prices carry `now()`, so they always win the `effective_at` guard. The reverse does not hold: switching back to `SOURCE=api` outside market hours returns the last close, whose timestamp is *older* than the simulated rows — so every upsert is silently rejected and the app keeps serving simulated prices while reporting `SOURCE=api`.

Avoid this by treating prices as fully derived state:

```sql
DELETE FROM latest_prices WHERE source = 'simulated';   -- plus flush price:*
```

~~The price service records the active source at startup and **refuses to start** if it does not match what is already in `latest_prices`, directing the operator to `make reset-prices`.~~ *Revised (2026-09-21): refusing both ways was wrong - only api-over-simulated is dangerous. The service now wipes simulated rows when starting the vendor, and adopts real rows as starting values when starting the simulator. Neither refuses.* Users, watchlists and securities are untouched, so the reset costs nothing. The startup check is the part that matters — without it this fails silently, which is the worst mode for something that quietly changes which numbers you are reporting.

**Show the source in the UI.** A small badge whenever `source != 'api'`. It costs nothing and means nobody watching a demo mistakes generated numbers for live market data.

**Auth.** One user, one default watchlist, created on registration. Argon2 or bcrypt password hash, HS256 JWT with a shared secret. The same secret signs Centrifugo connection tokens, so `POST /realtime/token` is a claims transform rather than a second auth system. This satisfies the brief without building an identity provider.

---

## 9. Price Cache and Read Path

```
price:NVDA -> {"security_id":123,"price":185.42,
               "effective_at":"2026-09-19T20:00:00Z"}
```

`MGET` the whole watchlist in one round trip.

### Read path

**Redis first, fall back to `latest_prices`.** Postgres is authoritative; Redis is a read-through cache in front of it. A miss, an expired key, or a Redis outage falls through and returns a correct answer — never a null. Drift self-heals on the next tick.

TTL must exceed the worst expected gap between updates for any ticker. Too short and quiet stocks expire between ticks, so every read for them falls through — the cache stops helping exactly where it was needed.

### Write ordering guard — both writes

Two price-service workers, or a retry after partial failure, can write an older price after a newer one. Neither store detects this on its own.

Guard **both** writes on `effective_at`:

* Postgres: the `WHERE excluded.effective_at > latest_prices.effective_at` clause in §8.
* Redis: a Lua compare-and-set reading the stored `effective_at` and writing only if the incoming one is newer.

Guarding Postgres alone is worse than guarding neither — the cache could then hold an older price than the table it caches, making the fallback path more correct than the fast path.

### Source toggle

```
LATEST_PRICE_SOURCE=redis|postgres
```

`postgres` bypasses the cache entirely. This exists to make the caching decision measurable rather than asserted: run Scenario D (reconnect storm — 200k snapshot lookups in a burst) both ways and report numbers. "Redis was Nx faster under a 200k-lookup burst" beats "Redis is faster." *Measured (2026-09-21, §8.4/§8.8 of measurements.md): N < 1 for the burst; under 25k sustained polling the cache cuts p99 3.5x and DB CPU 17 points at the cost of ~30 API points. On push it is nearly idle - a production version on push could drop it.*

---

## 10. Snapshot / Subscribe Ordering

Read-then-subscribe drops any update landing in the gap. On connect **and on every reconnect**:

1. subscribe to the `ticker:*` channels, buffer incoming events without applying
2. `GET /watchlist` for membership and snapshot prices
3. apply the snapshot
4. drain the buffer, discarding any event whose `effective_at` is older than the applied value

Cheap up front, painful to retrofit. Note in the README that the Redis engine also offers Centrifugo's own history/recovery — but snapshot-on-reconnect is simpler, works identically under both brokers, and is correct even after an outage longer than the history window.

---

## 11. Broker: Redis Default, NATS as the Experiment

**Default is Redis.** Centrifugo's Redis engine provides inter-node pub/sub, channel history and presence. *Corrected (2026-09-21): the original estimate of ~2k publishes/sec assumed 10k tickers. With 99 tickers and a 30% change ratio it is ~30 publishes per 5s tick — about 6 per second. That is 300x less broker traffic than planned, and it means the cache-vs-broker contention this section calls the most interesting question may simply not appear at this scale. Phase 3 should measure it and be prepared to report that it did not.* Adding a second messaging system to move 6 msg/s would be architecture for its own sake.

**But Redis is doing two jobs.** It is the snapshot cache and the broker, on one single-threaded event loop. Every publish competes with every `MGET` from a watchlist load. Under Scenario D, snapshot reads and fanout publishes contend directly. This is the most interesting scaling question in the system, and it is not answerable by reasoning.

**So make the broker swappable and measure it.**

```
make up                 # Redis engine (default)
make up BROKER=nats     # NATS broker via compose override
```

Because the price service publishes through the Centrifugo API, no application code changes — only `centrifugo/config.json` and which container is running.

What the comparison should establish:

* whether broker and cache traffic on one Redis instance measurably degrades snapshot latency under burst *- measured: no (§8.7). Redis CPU 3.7% with the broker, 3.6% without; every latency column within noise.*
* how much of any degradation is contention versus raw throughput
* what is given up with NATS: the NATS broker provides at-most-once pub/sub only, with no channel history or recovery — acceptable here because §10 re-fetches a snapshot on reconnect anyway

Also worth stating: in a clustered Redis deployment, classic pub/sub broadcasts every publish to every node, and Redis 7 sharded pub/sub fixes that but drops pattern subscriptions. Not reachable on a single laptop instance, but it is the reason a production version at this scale might land on NATS regardless of what the local benchmark shows.

### Channels

```
ticker:NVDA   ticker:AAPL   ticker:BTC
```

Clients subscribe only to the tickers in their watchlist. Prices are not user-private, so the `ticker:` namespace allows subscription by any authenticated client — no per-channel subscription tokens needed. Clients never get direct broker access.

**Do not use per-user channels.** One channel per user means millions of channels and one publish per interested user per tick. Per-ticker channels mean one publish and one broadcast. The cost is that a client with 10 stocks receives 10 messages per tick instead of one — which at this cadence is negligible and the right trade.

---

## 12. Hot Tickers and Fanout

Stock popularity is heavily skewed; the seeder generates a Zipf distribution (s=1.1). *Measured at 1M users: NVDA 958k watchers (9.9% of all subscriptions), TSLA 788k, AAPL 629k; ten tickers carry half of all subscriptions; the tail sits near 20k. Note the ranking had to be made explicit — ranking by security id ranked alphabetically and made Airbnb the second most-watched stock.*

**Be precise about where the difficulty actually lives.** Clients subscribe to `ticker:NVDA` directly, so watchlist membership never participates in fanout — it only matters for the initial snapshot. The 10M-row dataset is a *database sizing* exercise. And at a fixed 5s cadence, a hot ticker publishes exactly as often as a cold one; volatility no longer drives load. So the celebrity problem is largely solved by construction *for publishing*: one publish, one broadcast. *Review note (2026-09-21): fanout is still one socket write per subscriber, concentrated in one channel. At 25k connections NVDA's share means roughly every client subscribes to it, so one move is ~25k writes in a burst. Phase 3 measures per-channel broadcast cost and carries channel sharding (`ticker:NVDA:{0..N}`) as a pre-planned mitigation, built only if the measurement asks for it.*

What is actually worth measuring:

* **subscriptions per connection** — 20k connections × 10 tickers = 200k subscription objects. This is the Centrifugo memory driver.
* **per-channel broadcast cost** as subscriber count grows into the hundreds of thousands.
* **egress rate** at the hot channel, and the effect of the change-ratio parameter on it.

The backend publishes one message per ticker per tick regardless of watcher count. Never one message per user.

State all of this in the README — an interviewer will ask why the hot ticker was hard, and "it wasn't, once we picked per-ticker channels" is a stronger answer than a vague one.

---

## 13. Slow Consumers

A slow client must not cause unbounded memory growth. Centrifugo's actual mechanism is a bounded per-client queue and a **disconnect** on overflow — it does not conflate per client and keep the newest value.

So protection here is:

* publishing only changed tickers, which is the real reduction
* a configured client queue limit
* accepting that clients exceeding it are disconnected and recover via the §10 snapshot path

Do not write this as though per-client conflation exists. Count disconnects under Scenario E.

The 5s cadence makes this much less severe than a tick-by-tick feed would: a client must be more than a few seconds behind to be in trouble at all.

---

## 14. Client Application (React + Vite)

Two screens, intentionally unpolished.

**Login** — username and password, stores the JWT.

**Main** — one screen containing:

* search bar: debounced `GET /securities/search`, results with an add button
* watchlist: ticker, name, current price, last-updated indicator, remove button

**Transport abstraction.** Both modes sit behind one hook — `usePrices(securityIds)` — returning the same shape either way. The rest of the UI must not know which transport is active, or the comparison in §5 stops being like-for-like.

* `TRANSPORT=poll` — `setInterval` on `GET /watchlist` every 5s, with jitter on the first call so clients don't align. Build this first.
* `TRANSPORT=push` — `centrifuge-js` with the token from `POST /realtime/token`. Subscribe on mount following the §10 ordering; subscribe and unsubscribe incrementally when a stock is added or removed rather than resubscribing the whole list.

Show the active transport and a connection status indicator (connected / reconnecting / polling / offline) in the UI. During a demo it makes the toggle visible, and during load runs it confirms which mode a client is actually in.

Show a visible timestamp or flash on each price update. It is the simplest way to demonstrate the update path works under either transport, and it makes reconnect behavior observable.

No component library, no state management library, no routing beyond a logged-in check. The brief explicitly does not require polish, and time spent here is time not spent on the measurements that are the actual deliverable.

---

## 15. Search

Postgres only. Support `NV`, `NVDA`, `NVIDIA`. Rank: exact ticker → ticker prefix → name match. `pg_trgm` if useful. No Elasticsearch unless a measured requirement justifies it.

---

## 16. Seed Presets

### Securities and starting prices

Two static files in git, loaded by every preset:

```
seed/securities.csv    # the vendor's 99 symbols: ticker, name  (was "~10k"; corrected)
seed/prices.csv        # ticker, price, captured_at
```

Both are real. `securities.csv` matters because real symbols make search behave realistically — `NV` matches NVDA, NVAX, NVR — which a set of invented tickers would not. Never prefix or namespace simulated tickers; the synthetic thing here is the price, not the security.

`prices.csv` is captured **once**, as a one-off build step against the vendor API, and committed. It is not a runtime dependency and not a backup:

* the scale seed no longer needs the vendor at all, which matters because fetching 10k symbols against a rate limit is slow and may be impossible on an affordable tier
* benchmarks become reproducible from a file in git rather than from whatever the market did that day
* simulated mode random-walks from real starting values (§7), so prices stay plausible

Script it as `tools/seed/capture_prices.py`, run it once, commit the output, and note the capture date in the README.

### User presets

| preset  | users | watchlist rows |
|---------|-------|----------------|
| small   | 10k   | ~100k          |
| medium  | 100k  | ~1M            |
| million | 1M    | ~10M           |

~10 items per user, Zipf-distributed across securities. Bulk load via `COPY`. Never row-by-row.

---

## 17. Logical vs Physical Users

**Logical users** exercise database size, watchlist distribution, popularity skew, subscriber math.
**Real connections** exercise socket limits, Centrifugo memory, networking, fanout.

Keep them independently configurable. This distinction is essential and belongs in the README.

---

## 18. Environment Preparation — Before Any Connection Test

Connection-limit results are meaningless until the environment is prepared, and an unprepared run will misattribute an OS ceiling to Centrifugo.

```yaml
services:
  centrifugo:
    ulimits:
      nofile: { soft: 200000, hard: 200000 }
  load-generator:
    ulimits:
      nofile: { soft: 200000, hard: 200000 }
```

Host checks — macOS defaults can be hostile. *(This machine ships `ulimit -n` 1,048,576; the binding limit turned out to be 16,384 ephemeral ports, which caps a host-run generator at ~16k keep-alive clients and forced the container generator for anything larger.)*

```bash
ulimit -n
sysctl kern.maxfiles
sysctl kern.maxfilesperproc
```

Widen `net.ipv4.ip_local_port_range` in the load generator container: 20k outbound connections to one destination exhausts the ephemeral port range before anything else breaks.

The README must state that Docker Desktop interposes a VM and a network layer, so **a local connection ceiling is not a Centrifugo scalability ceiling.**

---

## 19. Load Scenarios

**A — Baseline.** 100k logical users, 10k clients, the 99-ticker catalog, 5s cadence, `SOURCE=simulated` with a stated change ratio.

**B — Transport Comparison.** Scenario A load under `TRANSPORT=poll` and `TRANSPORT=push`, identical in every other respect. Report request rate, API CPU and worker count, p50/p99 update latency, and bytes on the wire per client per minute. **This is the headline experiment** — it is the evidence for the brief's stated focus, and it is what justifies Centrifugo existing at all.

Then push the polling side until it breaks: raise client count until p99 degrades or workers saturate, and record where. That number is the answer to "why not just poll."

**C — Million Users.** 1M logical, 10M watchlist rows, 20k connections.
**D — Reconnect Storm.** Drop a large share of clients simultaneously and reconnect. The snapshot-burst test — run under both `LATEST_PRICE_SOURCE` values and both brokers.
**E — Slow Consumers.** A percentage read deliberately slowly; count queue-overflow disconnects. Push only; polling has no equivalent, which is itself worth noting.
**F — Celebrity Ticker.** One ticker with 500k logical watchers; measure per-channel broadcast cost as subscriber count climbs.
**G — Postgres Slowdown.** Artificially slow `latest_prices` upserts. Delivery and cached snapshot reads should stay healthy under both transports.
**H — Redis Restart.** With the Redis engine this takes down cache *and* broker at once — a direct consequence of the default topology. Compare against `BROKER=nats`, where only the cache is lost.
**I — Broker Comparison.** Scenarios A and D under Redis and under NATS, same load, side by side.

---

## 20. Metrics

**price-service**
```
price_updates_received_total
price_updates_changed_total      # after change detection
price_updates_published_total
publish_latency_seconds
latest_prices_upsert_seconds
cache_write_seconds
tick_duration_seconds            # must stay well under 5s
```

**API** — `http_requests_total`, `http_request_duration_seconds`, snapshot-endpoint latency broken out, cache hit/miss ratio, and worker saturation. Under `TRANSPORT=poll` these are the numbers that matter most; the whole cost of polling lands here.

**Transport comparison** — the Scenario B table needs these on both sides, labelled by transport:
```
client_update_latency_seconds    # price change -> visible in client
bytes_per_client_per_minute
api_requests_per_second
api_cpu_seconds_total
```
Measure update latency the same way in both modes, at the client, or the comparison is not like-for-like.

**Realtime (Centrifugo)** — `connections_current`, `subscriptions_current`, `messages_published_total`, `messages_sent_total`, client queue overflow disconnects

**Hot ticker** — subscribers and delivery rate for a *fixed* benchmark ticker set or buckets. Never an unbounded ticker label.

**Redis** — command latency, ops/sec, and under the default topology a breakdown of broker versus cache traffic if obtainable. This is the contention evidence.

**Postgres** — upsert latency, search latency, watchlist lookup latency, connections

`tick_duration_seconds` is the one to watch first: when a 5s tick takes longer than 5s to process, the system is no longer meeting the brief, whatever else the dashboards say.

---

## 21. Failure Experiments

* **Redis down, then back (default topology)** — cache *and* broker lost together: snapshot reads fall through to `latest_prices` and stay correct, realtime stops, clients reconnect and re-snapshot on recovery. The price service warms the cache from `latest_prices` on startup. Measure the snapshot-latency spike during the empty window.
* **Redis down under `BROKER=nats`** — only the cache is lost; realtime continues. The contrast is the point.
* **Centrifugo down** — clients reconnect (to another node when clustered); state refreshed via `GET /watchlist`.
* **Postgres down** — realtime and cached snapshot reads continue; `latest_prices` upserts retry against a bounded buffer with a `dropped_total` counter.
* **price-service restart** — recovers cleanly, resumes publishing within one tick, warms the cache on boot.

---

## 22. Questions the Case Study Must Answer

1. At what client count does 5-second polling stop being the right answer — and what does push cost in exchange? Answer with the Scenario B table, not an argument.
2. Can Postgres support 1M users and 10M watchlist rows?
3. What is watchlist snapshot latency at scale, cached and uncached?
4. How many realtime connections can one Centrifugo node sustain locally?
5. Does running broker and cache on one Redis instance measurably degrade snapshot latency under burst — and does swapping to NATS remove it?
6. How does per-channel broadcast cost scale as one ticker approaches 500k subscribers?
7. What happens to slow clients, and how many are disconnected?
8. Does `latest_prices` persistence affect realtime latency?
9. What resource becomes the first bottleneck locally — file descriptors, Docker networking, CPU, memory, Redis event loop, Centrifugo fanout, or Postgres — and how do we distinguish an infrastructure limit from an application limit?
10. How would this scale horizontally in production?

### Method for question 9

Without a discriminator this stays rhetorical. Use two:

* **Run the same load with the generator on the host, then inside a container.** If the ceiling moves, it is environmental — Docker networking or VM limits — not application.
* **Pin Centrifugo to fewer CPUs and rerun.** If the ceiling moves proportionally, it is CPU-bound application work; if not, the limit is elsewhere.

Record both. An argued answer is worth much less than a measured one.

---

## 23. Out of Scope, and Deliberate Cuts

Do not introduce unless a load test exposes a concrete requirement: Kafka, Redpanda, JetStream, Celery, Temporal, Elasticsearch, Kubernetes, service mesh, or a custom WebSocket gateway.

**Price history was cut deliberately.** An earlier draft included a partitioned `price_history` table with retention management. The brief asks only that *user data* persist across restarts and that users see *current* prices — nothing requires a time series. Building it would have added a partitioning and retention story that answers no question the case study asks.

Say this in the write-up rather than silently omitting it, and note what it would enable: price charts over time, portfolio value history, backtesting, and analytics. The extension is a partitioned append-only table alongside `latest_prices`, with partition granularity chosen from the retention window — monthly for a multi-year production dataset. Deciding not to build something for a stated reason reads better than not having considered it.

**Market-session behavior** (equity hours, pre/post market, crypto always-on) is likewise out. Interesting product logic; dilutes the scaling question.

**Future extensions:** EKS deployment of Centrifugo and the API, KEDA autoscaling on connection count, price alerts (which would genuinely justify durable workflow orchestration), and sub-second updates, which the push architecture already supports.

---

## 24. Compose Profiles and Makefile

Core: `postgres redis api price-service client`
Profiles: `push` (Centrifugo), `nats`, `load`, `obs`

Centrifugo is a profile, not a core service — Phase 1 runs without it, which keeps the polling version honestly standalone rather than push-minus-a-flag. There is no simulator profile: the simulated source is a mode of `price-service`, not a separate container.

```
make up                              # core stack, TRANSPORT=poll, SOURCE=api
make up SOURCE=simulated             # generated prices (offline / benchmarks)
make up TRANSPORT=push               # adds Centrifugo (Redis engine)
make up TRANSPORT=push BROKER=nats   # swaps Centrifugo's broker
make down / restart / logs
make seed-small / seed-medium / seed-million
make reset-prices                    # clear latest_prices + price:* on source switch
make capture-prices                  # one-off: refresh seed/prices.csv from the vendor
make obs
make test / lint / format / clean
```

Load targets are deliberately separate and never run as part of `make up`. Each checks the stack is healthy first and refuses to run otherwise:

```
make load CLIENTS=20000 LOGICAL_USERS=1000000
make bench-transport                 # Scenario B: same load, poll vs push
make bench-broker                    # Scenario I: Redis vs NATS broker
```

`bench-*` targets force `SOURCE=simulated` and a fixed `SIM_SEED` regardless of the ambient setting — a benchmark against live market data is not reproducible, and letting it run silently would quietly invalidate every number in the README.

---

## 25. Repository Layout

```
stock-watchlist/
├── docker-compose.yml
├── docker-compose.nats.yml      # broker override
├── Makefile
├── README.md
├── .env.example
├── services/
│   ├── api/                     (app/, Dockerfile, pyproject.toml)
│   └── price-service/           (app/, sources/{api,simulated}, Dockerfile)
├── client/                      (React + Vite, Dockerfile)
├── shared/                      (models/, db/, cache/, metrics/)
├── tools/                       (load_generator/, seed/)   # not part of the product
├── centrifugo/
│   ├── config.redis.json
│   └── config.nats.json
├── seed/                        # securities.csv, prices.csv (committed data)
├── migrations/
├── monitoring/                  (prometheus/, grafana/)
└── tests/                       (unit/, integration/, load/)
```

---

## 26. Implementation Order

The order is the argument. Polling ships first and gets measured; push is introduced as the response to a measured limit, not as an assumption.

**Phase 1 — working product on polling**

1. **Core service** — Postgres, Redis, FastAPI, login, securities, watchlist CRUD, search. Validate with curl.
2. **price-service** — 5s publish tick, `SOURCE=api` first, union-of-watchlisted ticker set, change detection, guarded cache write, guarded `latest_prices` upsert, source-mismatch startup check.
3. **Snapshot endpoint** — `GET /watchlist` with Redis-then-Postgres fallback and the `LATEST_PRICE_SOURCE` toggle.
4. **React client on `TRANSPORT=poll`** — login, search, watchlist, prices refreshing on a jittered 5s interval behind the `usePrices` hook.

**The brief is satisfied here.** Everything below exists to answer whether this design holds at the stated scale.

**Phase 2 — find the limit**

5. **Simulated source** — capture `seed/prices.csv` once, then random-walk from it: volatility, change ratio, skew, RNG seed. Needed before any benchmark, since the vendor cannot supply load-scale data.
6. **Seed and benchmark** — 1M users, 10M rows; search, watchlist lookup, snapshot latency.
7. **Load generator** — polling clients first. Do §18 first, then raise client count until p99 degrades or API workers saturate. **Record where polling breaks.** That number is the justification for everything in Phase 3.

**Phase 3 — push, and prove it was worth it**

8. **Centrifugo (Redis engine)** — token flow, `ticker:` namespace, publish from the price service via the API.
9. **`TRANSPORT=push` in the client** — `centrifuge-js` behind the same hook, with §10 ordering on connect and reconnect.
10. **Scenario B** — identical load under both transports, table recorded.
11. **Load generator: real connections** — reconnect storm, slow consumers, celebrity ticker.
12. **NATS broker override** — config only; run Scenario I.
13. **Observability** — Prometheus, Grafana, dashboards.

If time runs short, stopping after step 7 still yields a complete working product plus a measured limit — which is a better outcome than a half-finished push implementation with no numbers.

---

## 27. README Narrative

Write it as the sequence the work actually followed. The arc is: simplest thing that works → where it broke → what replaced it → what that cost.

1. **What this is** — an instrumented case study on one laptop, non-goals from §1 stated up front.
2. **Requirements** — millions of users, own watchlist each, 5s price updates, persistence, login, one screen.
3. **Version 1: polling** — the whole product on `setInterval`, and why that is a legitimate answer to the brief rather than a strawman.
4. **Where polling broke** — the measured client count, what saturated first, and the arithmetic that makes it inevitable at the stated scale.
5. **Version 2: push** — Centrifugo, per-ticker channels, and the Scenario B table. What push cost: connection state, reconnect ordering, slow-consumer handling, one more component.
6. **Separation of concerns** — Postgres for durable user data, Redis for snapshot cache and broker, Centrifugo for client fanout.
7. **Channel design** — per-ticker not per-user, and why that makes the hot-ticker problem mostly disappear.
8. **The snapshot path** — ordering on connect and reconnect, the guard on both writes, and the `LATEST_PRICE_SOURCE` numbers rather than an assertion that Redis is faster.
9. **The broker experiment** — one Redis doing two jobs, what contention was measured, what the NATS swap changed, and what it cost.
10. **What was deliberately not built** — price history and why (§23).
11. **Environment caveats** — §18 in full, so no reader mistakes a laptop ceiling for a product ceiling.
12. **Measured results** — real numbers from the developer machine. **Never invent performance numbers.**

---

## 28. Definition of Done

1. `make up` starts the core stack; `make up BROKER=nats` swaps the broker with no application code change.
2. Users can register and log in; the session survives a page reload.
3. Users can search stocks by ticker and by company name.
4. Users can add and remove stocks from their watchlist.
5. Watchlists and membership survive a full stack restart.
6. The client shows current prices on load, before any update arrives.
7. Prices update in the client every 5 seconds without a page refresh, under **both** transports.
8. `TRANSPORT=poll|push` switches the client with no change to the rest of the UI, and the active transport is visible on screen.
9. Scenario B has been run: identical load under both transports, with the comparison table in the README, plus the client count at which polling degrades.
10. Snapshot reads hit Redis and fall through to `latest_prices` on miss or outage, never returning null.
11. Both latest-price writes are guarded on `effective_at`; an out-of-order replay leaves neither stale.
12. `LATEST_PRICE_SOURCE` toggles the read path, and Scenario D has been run both ways with numbers recorded.
13. Clients follow subscribe-buffer-snapshot-drain on connect and reconnect.
14. Only changed tickers are published; the reduction is measured.
15. Slow clients cannot cause unbounded memory growth; disconnects are counted.
16. 1M logical users and ~10M watchlist rows can be generated.
17. Socket count is configurable independently of logical users.
18. Scenario I has been run and the broker comparison is in the README with numbers.
19. Seeded securities and starting prices come from committed CSVs; seeding needs no vendor call.
20. Switching `SOURCE` requires `make reset-prices`, and the price service refuses to start on a source mismatch.
21. Centrifugo, NATS, the load generator, Prometheus and Grafana are all optional profiles; `make up` runs the polling stack without them.
22. Environment limits (§18) are configured and documented before any connection benchmark.
23. The README documents architecture, tradeoffs, deliberate cuts, bottlenecks, and measured results — including the question 9 discriminator runs.
