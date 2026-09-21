# Stock Watchlist

A stock watchlist service and client for the Albert product engineering case
study. Users log in, search stocks by ticker or company name, keep a watchlist,
and see prices update every 5 seconds — pushed over WebSockets, with a polling
implementation kept alongside as the baseline it was measured against.

This README is written as the sequence the work actually followed: the
simplest thing that worked, where it broke, what replaced it, and what that
cost. Every number in it was measured on one laptop and says so where the
laptop, rather than the design, set the limit.

The original brief is preserved at [`archive/ORIGINAL_README.md`](archive/ORIGINAL_README.md);
the implementation plan, with the sections that measurement overturned
corrected in place and dated, is [`plans/ticker-watch-plan.md`](plans/ticker-watch-plan.md).

---

## Quick start

```bash
cp .env.example .env        # paste ALBERT_API_KEY from the case study email
make bootstrap              # build, start, migrate, create demo users
make open-app               # http://localhost:3000  ->  user1 / password
```

A fresh volume is seeded from `db/demo.sql` on first start, so the watchlist
is populated before you click anything. `make help` lists every target.

| | |
|---|---|
| client | http://localhost:3000 |
| API + Swagger | http://localhost:8000/docs |
| API metrics | http://localhost:8000/metrics |
| price-service metrics | http://localhost:8002/metrics |
| Centrifugo health / metrics | http://localhost:8001/health · /metrics |

**Shipped defaults:** push transport, Redis as Centrifugo's engine, one
Centrifugo node, the real vendor as price source, a single hot-reloading API
process. Polling, NATS, extra nodes and multiple workers exist as toggles for
the comparisons below, not as alternatives.

**Markets are closed most of the time.** Outside trading hours the vendor
returns the last close and nothing moves on screen — correct, and hard to
review. `PRICE_SOURCE=simulated` in `.env` then `make restart` random-walks
from the last real prices; the UI shows a `simulated prices` badge so nobody
mistakes them for live data. The service reconciles stored prices itself on
the switch, either direction.

**Editing `.env` or code:** `make restart`. Plain `docker compose restart`
keeps old config and `up -d` keeps old code under a bind mount; `make restart`
force-recreates and renews volumes, because both of those bit this project.

**Submitting:** `make submit` refuses a `.env` left in benchmark mode and
packages `solution.zip`. See *Before you submit* at the end.

---

## The brief, and where each requirement is met

| requirement | where |
|---|---|
| search stocks by name or ticker, add to a watchlist | `GET /securities/search`, `POST /watchlist/items` — ranked exact-ticker → prefix → name |
| remove from the watchlist | `DELETE /watchlist/items/{id}` |
| see current prices | `GET /watchlist` returns membership **and** prices in one call |
| **prices update every 5 seconds** | the price service ticks every 5s; changed tickers are pushed to subscribed clients within ~100ms of the tick (measured below) |
| designed for millions of users, each with a watchlist | 1M users / 9.7M rows seeded and measured; read latency flat from 10k to 1M (§*Where polling broke*) |
| user data persisted and reused across restarts | Postgres volume; demo state also shipped as `db/demo.sql` and loaded on first start |
| focus on architecture and service↔client communication | the transport comparison is the spine of this document |
| login, one screen, unpolished UI | bcrypt + short-lived JWT with rotated refresh tokens; one screen |
| treat the stock API as paid, limit calls | **one** vendor call per tick for the whole catalog, from one process, for any number of users |

---

## Architecture

```
                 Albert API  (or the simulator)
                       │  one call per 5s tick, all 99 tickers
                       ▼
                price-service                 the only writer of prices,
                       │                      the only caller of the vendor
        ┌──────────────┼──────────────────┐
        ▼              ▼                  ▼
  Redis price      Postgres           Centrifugo  ──►  Redis engine  ──►  WebSocket clients
  cache            latest_prices      (publish per        (inter-node        (one channel
  (all tickers,    (changed rows       changed ticker)     pub/sub)           per ticker)
   guarded)         only, guarded)

  Client load / reconnect:   FastAPI ──► Postgres (membership) ──► Redis, fallback Postgres (prices)
```

| component | owns | is not in |
|---|---|---|
| **FastAPI** (`api`) | login, search, watchlist CRUD, the snapshot, Centrifugo connection tokens | the realtime path |
| **Postgres** | users, securities, watchlists, membership, `latest_prices` — the source of truth | the realtime path |
| **Redis** | read-through cache for the snapshot; Centrifugo's engine | authority for anything |
| **price-service** | one tick loop: read source → detect change → write cache, upsert Postgres, publish | serving clients |
| **Centrifugo** | connections, subscriptions, reconnects, fanout | business logic |
| **React + Vite** | login and one screen, behind one transport-agnostic hook | — |

**Three delivery paths, three guarantees.**

- *Realtime* — at-most-once. A dropped update is superseded within 5 seconds. No replay.
- *Snapshot* — must be correct. Redis with Postgres behind it; never a null.
- *Durable* — users, watchlists, membership, `latest_prices`. Survives restart.

**A client never waits for a realtime event to learn a current price.** On
connect, and on every reconnect, it subscribes first, takes one snapshot,
applies it, then drains anything buffered that is newer. Read-then-subscribe
would drop whatever landed in the gap.

---

## Version 1: polling

`GET /watchlist` already returns membership and prices together, so polling
is a fixed-phase 5-second timer on the client and no server work at all. It is
simple, stateless, scales behind any load balancer, survives every proxy, and
needs no reconnect logic. It satisfies the brief. It was built first, on
purpose, so that push would be introduced as the answer to a measured limit
rather than assumed.

The client jitters its first poll and schedules from the previous *start*, not
the previous completion, so the interval stays 5s and clients do not drift
into synchronised spikes — the same timing the load generator uses, so the
comparison is like for like.

---

## Where polling broke — measured

Full method and every table: [`docs/measurements.md`](docs/measurements.md).

**The database was never the problem.** 1M users, 9.7M watchlist rows,
seeded with `COPY` in 136s:

| operation (p50) | 10k users | 100k | 1M |
|---|---|---|---|
| search | 0.16ms | 0.16ms | 0.16ms |
| membership | 0.13ms | 0.12ms | 0.14ms |
| snapshot | 0.17ms | 0.17ms | 0.17ms |

Every read is a point lookup on an indexed key; row count does not enter.

**The API was.** 4 uvicorn workers, simulated prices, one batch, after the
review below removed a write from the read path:

| clients | req/s | p50 | p99 | errors | API CPU |
|---|---|---|---|---|---|
| 20,000 | 3,772 | 3.8ms | 27.7ms | 0 | 280% |
| 25,000 | 4,712 | 4.6ms | 36.5ms | 0 | 343% |
| **30,000** | **5,653** | **5.5ms** | **65.7ms** | **0** | **382%** |
| 32,500 | 4,433 | 69ms | 17,964ms | 87 | 408% of 400% |

Congestive collapse, not a plateau: past the knee throughput *falls*, p50
rises hundreds-fold, requests queue in memory. The API exhausts its worker
budget first; Postgres is at ~130%, Redis at ~13%.

**And update latency never improved with any of it.** Measured at the client,
from a price's `effective_at` to when the client sees it:

| clients | p50 | p95 | p99 |
|---|---|---|---|
| 1,000 | 2,548ms | 4,763ms | 4,970ms |
| 25,000 | 2,573ms | 4,763ms | 4,961ms |

Half an interval at p50, a full interval at p99, at every load. It is
arithmetic: a change lands at a random point in a fixed cycle. Holding load at
1,000 clients and varying only the interval confirms it — 504 / 1,005 / 2,535 /
4,604ms p50 at 1 / 2 / 5 / 10s. **No amount of server capacity touches it.**

**Neither lever rescues polling.** More workers scale sub-linearly (936 →
2,058 → 3,743 → 5,188 req/s at 1 → 8 workers, with p99 degrading first).
Shortening the interval buys latency at exactly proportional cost in clients:
the server responds only to request rate — the same ~2,810 req/s at 5s/15,000
clients, 2s/6,000, 1s/3,000. Sub-second latency by polling means one stack per
~5,000 clients: **200 stacks per million**, to deliver data that at a 30%
change ratio is ~70% unchanged.

That is two arguments. Update latency is a *capability* argument no budget can
overrule; per-request cost is a *cost* argument that can. The first decided the
architecture; the second made it cheaper as well.

---

## Version 2: push

Centrifugo v6 behind a `push` Compose profile. The price service publishes one
message per **changed** ticker per tick, batched into one HTTP call to
Centrifugo's API; clients hold a WebSocket and subscribe to `ticker:<T>`
channels. Nothing is published for a ticker that did not move.

Same load under both transports, one batch, simulated prices at a 30% change
ratio:

| clients | transport | HTTP req/s | upd p50 | upd p95 | upd p99 | errors | API CPU peak / **steady** | DB CPU peak / **steady** | Redis | Centrifugo peak / steady | B/client/min |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5,000 | poll | 943/s | 2448ms | 4769ms | 4955ms | 0 | 109% / 84% | 28% / 19% | 3% | 0% / 0% | 16,559 |
| 5,000 | **push** | 111.0/s | **70ms** | **135ms** | **164ms** | 0 | 106% / **2%** | 27% / **1%** | 4% | 39% / 10% | **6,280** |
| 10,000 | poll | 1883/s | 2552ms | 4768ms | 4946ms | 1 | 277% / 192% | 95% / 58% | 9% | 0% / 0% | 16,536 |
| 10,000 | **push** | 221.7/s | **133ms** | **286ms** | **331ms** | 0 | 242% / **2%** | 26% / **1%** | 7% | 59% / 16% | **6,268** |
| 20,000 | poll | 3763/s | 2586ms | 4770ms | 4966ms | 0 | 398% / 375% | 114% / 104% | 11% | 1% / 0% | 16,528 |
| 20,000 | **push** | 439.5/s | **273ms** | **671ms** | **734ms** | 0 | 358% / **2%** | 68% / **0%** | 10% | 131% / 20% | **6,598** |
| 25,000 | poll | 2524/s | 4395ms | 13418ms | 19265ms | 452 | 412% / 401% | 105% / 88% | 11% | 2% / 0% | 8,898 |
| 25,000 | **push** | 549.6/s | **330ms** | **602ms** | **894ms** | 0 | 332% / **5%** | 61% / **0%** | 8% | 118% / 26% | **6,397** |

CPU columns are the peak sample over the run and the **mean over the second
half**, once every client is connected. The steady-state column is the one
that matters: under push the API sits at **2–5%** and Postgres at **0–1%**
after the connect ramp, against 81–401% and 24–104% under polling at the same
client counts. Push's CPU lives in Centrifugo, at 10–26% steady state.

In this batch the 25,000-client polling row collapsed (452 errors, p50 4.4s)
where an earlier batch had it clean at 4,706 req/s — the ceiling moves with
the machine's state across a long session, which is why comparisons are only
made within a batch. In the *same* batch, 25,000 push clients ran with zero
errors and a 330ms p50.

Polling's latency is flat because it is the interval. Push's grows with
subscribers because it is fanout — ~13,400 deliveries a second at 25,000
clients, at about **0.12ms of Centrifugo CPU each**, against the 0.44ms the
polling arithmetic said it had to beat. HTTP request rate under push is one
snapshot per connection and then nothing.

**What push cost, stated honestly:** connection state; a subscribe-then-
snapshot ordering rule on every connect; slow-consumer handling; a fanout cost
that grows with subscribers; one more component. Those are real, and they are
why Centrifugo is here rather than hand-rolled WebSockets.

---

## Channel design and the hot ticker

Channels are **per ticker, never per user.** One publish per changed ticker,
however many users watch it; the broker fans out. The cost is that a client
with ten stocks receives ten messages per tick instead of one, which at this
cadence is negligible. Prices are not user-private, so any authenticated
connection may subscribe to any `ticker:*` channel — no per-channel tokens.

That removes the expensive half of the celebrity problem by construction: NVDA
with 958,000 logical watchers costs the price service exactly what a ticker
with twenty costs. The other half — one socket write per subscriber,
concentrated in one channel — was measured, with every client subscribed to
NVDA and Centrifugo's own broadcast histogram as the source:

| NVDA subscribers | broadcast per publication | hot channel vs all channels |
|---|---|---|
| 5,000 | 0.7ms | within 5% |
| 20,000 | 6.4ms | within 5% |
| 25,000 | 6.2ms | within 5% |
| **50,000** | **~40ms** (steady state) | **p50 +39%** |
| 100,000 | — | the laptop saturated; not a measurement |

Clean to ~25k; superlinear by 50k, reproduced in a second full run within 1%.
**Three Centrifugo nodes on the same machine split CPU three ways and changed
nothing the client could see** — per-node broadcast time did not fall. A
local Docker experiment can verify the distribution mechanism, which it did,
but neither warrants "more nodes help" nor refutes it: three containers share
one VM's cores and network. On cloud infrastructure the same lever could be
load-bearing; that is where it has to be measured. Sharding
the hot channel is out of scope and written down as a discussion point.

---

## The snapshot path and what the cache is for

`GET /watchlist` reads Redis first (one `MGET` for the whole watchlist) and
falls through to `latest_prices` for anything missing, expired, or when Redis
is down — verified by stopping Redis mid-session: prices intact, path reported
as `postgres (redis unavailable)`, back on Redis within one tick of its
return.

**Both price writes are guarded on `effective_at`.** Postgres:
`WHERE excluded.effective_at > latest_prices.effective_at`. Redis: a Lua
compare-and-set that rejects anything *strictly* older, so the service can
rewrite every ticker each tick to refresh TTLs. Guarding only Postgres would
make the fallback more correct than the fast path.

**What the cache turned out to be for.** It is slower than Postgres for a
single snapshot (0.17 vs 0.14ms — 98 rows in `shared_buffers` beat a network
hop) and no faster under a 5,000-client reconnect storm (p99 22ms either way).
Under 25,000 *polling* clients it cuts request p99 3.5x and takes 17 points off
Postgres while adding ~30 to the API. Under push it is nearly idle. It stays: a
laptop where Postgres and Redis are both in-memory neighbours cannot say what
a managed Postgres across a subnet would cost, and on cloud infrastructure the
cache could be load-bearing for the reasons the plan gave. The local result is
"not needed here", not "not needed"; the toggle (`LATEST_PRICE_SOURCE`) is
there to measure it again where it might be.

---

## The broker experiment

Redis serves as both the price cache and Centrifugo's engine. The plan called
that contention "the most interesting scaling question in the system" and
made the broker swappable to measure it: `make up BROKER=nats` mounts a
different Centrifugo config and adds a NATS container, with **no application
code changed** — the price service only ever talks to Centrifugo's API.

Same 10,000-client load and 5,000-client reconnect storm under both:

| broker | update p50 / p99 | storm snapshot p99 | Redis CPU |
|---|---|---|---|
| Redis engine | 127 / 307ms | 22.0ms | 3.7% |
| NATS broker | 133 / 267ms | 22.3ms | 3.6% |

Indistinguishable. At ~30 changed tickers per tick the broker moves about six
messages a second; there is no contention to remove. The swap proved the
architecture's claim about itself and bought nothing. Redis stays.

---

## Slow consumers and reconnect storms

**Slow consumers.** Centrifugo's mechanism is a bounded per-client queue and a
disconnect on overflow — no per-client conflation, and none is pretended. With
10% of 2,000 clients blocking 3s per message and the queue lowered to 8 KiB,
Centrifugo disconnected 155 of ~200 (code 3012); everyone else's p50 stayed at
39ms. A disconnected client recovers through the same subscribe-snapshot-drain
path as any reconnect.

**Reconnect storm.** 5,000 of 10,000 clients dropping and reconnecting at
once — 5,000 snapshots in a burst — completed with zero errors and a 22ms p99.
The ordering rule cost nothing measurable.

---

## Auth

Username and password, bcrypt-hashed, exchanged for two tokens: a **15-minute
HS256 access token** verified from its signature alone, and a **30-day opaque
refresh token**, stored hashed, rotated on every use. A replayed refresh token
revokes every session the user holds; logout retires one; deleting a user
cascades their tokens away, so a session ends within one access-token TTL.

The access token doing no database read is what keeps every poll free of one.
The same secret signs Centrifugo connection tokens, so `POST /realtime/token`
is a claims transform, not a second auth system. The scaffold shipped "a login
method with no authentication"; the brief only says *support login*. This was
built because per-user watchlists are the premise, and because push needed a
signed token regardless.

---

## Data model

```
users ──< watchlists ──< watchlist_items >── securities ──1:1── latest_prices
                                                                 refresh_tokens
```

`latest_prices` is one row per security, overwritten each tick — ~100 rows,
entirely in memory, the durable snapshot source. `DOUBLE PRECISION`, not
`NUMERIC`: a display value, not a ledger entry. `source` is plain `TEXT`,
unindexed; two values do not justify a join.

**Migrations** are plain idempotent SQL in `migrations/`, applied in order by
`make migrate`. No Alembic: one schema, one environment, built from scratch.
`db/demo.sql` is regenerated from them by `make db-dump`, never hand-edited.

---

## Assumptions register

Each one: where it came from, and what would change if it were wrong.

1. **The catalog is 99 tickers.** Measured against the vendor. The plan assumed
   ~10k. If it were 10k: one call still covers it (the endpoint takes the whole
   list), search needs the `pg_trgm` indexes that today are inert, and the
   simulator's change ratio becomes the dominant load lever.
2. **One vendor call covers the universe.** Measured: all 99 in one request,
   ~180ms. Removes plan §7's "poll the union of watchlisted tickers" entirely.
3. **`effective_at` is our observation time.** The vendor returns no
   timestamp. If it did, the guards would compare vendor time and the
   source-switch reconciliation would need to account for clock skew.
4. **Vendor call volume is constant.** One process, one call per
   `UPSTREAM_POLL_INTERVAL_SECONDS`, for any number of users — the answer to
   "treat it as paid". Raising the interval cuts spend with no client change.
5. **30% of tickers change per 5s tick** (`SIM_CHANGE_RATIO`). Chosen before
   seeing live data; live market hours showed 25–45%. Push's egress saving
   scales with what *doesn't* change: 2.4x at 30%, ~1.1x observed at ~40%.
6. **Watchlist popularity is Zipf (s=1.1) with an explicit ranking.** NVDA
   9.9% of subscriptions, ten tickers = half. Ranking by id was alphabetical
   and made Airbnb the second most-watched stock; the fix is a `POPULARITY`
   list. Load follows the shape, so the numbers did not change; the demo did.
7. **Ten tickers per watchlist.** Seeder constant. Fanout deliveries scale
   with it linearly.
8. **Docker Desktop is the environment.** A VM and a port forwarder sit in
   every measurement. Host-generated load adds ~85ms at p99 with identical
   throughput; the host has 16,384 ephemeral ports; ten vCPUs are shared by
   every container including the load generators. Every ceiling here is a
   laptop ceiling first.
9. **Absolute throughput drifts across a long session** (thermal, inferred).
   Only within-batch comparisons are made; every table is one batch.
10. **A stateless access token is trusted for its lifetime.** Revocation lag is
    one TTL (15 min), closed by the refresh token. Shorter TTL or a denylist
    would tighten it.
11. **Redis has no persistence.** It is a cache the next tick refills and an
    engine whose history is unused. A restart costs one tick of cache misses.
12. **The load generator shares the machine.** It consumed ~0.5–2 cores per
    25k clients. Numbers at 50k and above are shaped by that; 100k is stated
    as unmeasurable here for that reason.

---

## Deliberately not built

- **Price history.** The brief asks for current prices and persisted *user*
  data. A partitioned `price_history` table would add a partitioning and
  retention story that answers no question the case study asks. Extension:
  append-only, monthly partitions; enables charts, portfolio history,
  backtests.
- **An identity provider.** Keycloak or any OIDC provider is where auth goes in
  production, replacing `users`, `/auth/login` and the shared HS256 secret with
  JWKS. Left out because it is a large container with a realm import to
  bootstrap and answers nothing the brief asks.
- **Hot-channel sharding.** Measured as unnecessary below ~25k subscribers per
  node; kept as a discussion point with the numbers.
- **Market-session behaviour.** Interesting product logic; dilutes the scaling
  question.
- **Kafka, Celery, Temporal, Elasticsearch, Kubernetes.** No measurement asked
  for any of them.

---

## Code layout

Layered by domain, the same three files per feature, so "how does search
work" is one directory.

```
src/watchlist/
  modules/{auth,securities,watchlist,prices,realtime}/
      {domain}_controller.py   # raw SQL, no business logic
      {domain}_handler.py      # business logic, domain errors
      {domain}_schema.py       # pydantic models
  api/                         # FastAPI app, deps, routers (transport only), /metrics
  price_service/               # the tick loop and its source adapters
  shared/                      # settings, db pool, cache, metrics, logging, security, timeutil
client/src/                    # usePrices dispatches on TRANSPORT to usePollPrices / usePushPrices
centrifugo/                    # config.redis.json, config.nats.json
tools/                         # migrate, seed, dump, submit-check, load generator (Go), bench harnesses
```

Routers contain no queries and no logic; handlers raise domain errors, never
`HTTPException`. Classes appear where several steps carry state — the tick
loop, the snapshot reader, the watchlist view, the source adapters — and
functions everywhere else. One way to get a logger (`shared.logging.get_logger`,
enforced by ruff).

---

## Configuration

Everything is in `.env`; `.env.example` documents each. The ones that change
behaviour:

| variable | default | |
|---|---|---|
| `ALBERT_API_KEY` | — | required for `PRICE_SOURCE=api` |
| `PRICE_SOURCE` | `api` | `simulated` for out-of-hours demos and every benchmark |
| `TRANSPORT` | `push` | `poll` is the comparison baseline |
| `BROKER` | `redis` | `nats` swaps Centrifugo's broker; config only |
| `NODES` (make) | `1` | `make up NODES=3` adds two Centrifugo nodes |
| `LATEST_PRICE_SOURCE` | `redis` | `postgres` bypasses the cache |
| `UVICORN_ARGS` | empty (`--reload`) | `--workers 4` for benchmarks — set in `.env`, not inline |
| `PUBLISH_INTERVAL_SECONDS` | `5` | what the product promises |
| `UPSTREAM_POLL_INTERVAL_SECONDS` | `5` | what the vendor costs |
| `PRICE_CACHE_TTL_SECONDS` | `60` | must exceed the worst gap between updates |
| `ACCESS_TOKEN_TTL_SECONDS` / `REFRESH_TOKEN_TTL_SECONDS` | `900` / `2592000` | |
| `DB_POOL_MAX_SIZE` | `16` | **per process**; workers × this must fit `max_connections` (200) |
| `POSTGRES_HOST_PORT` / `REDIS_HOST_PORT` | `55432` / `56379` | avoid clashing with a local Postgres or Redis |

---

## Tests

```bash
make up-detached && make migrate    # the integration tests need the stack
make test                           # 79 tests, separate watchlist_test database
make lint
```

They cover the claims this document makes: both write guards, the read path
and its fallback, handler-layer isolation between users, refresh-token
rotation and reuse detection, source reconciliation in both directions, the
review regressions (a resolve performs no UPDATE; `.000000` timestamps sort
correctly; the pipelined cache write actually queues), and that every metric
the docs quote is registered.

---

## Reproducing the measurements

```bash
# In .env: PRICE_SOURCE=simulated and UVICORN_ARGS=--workers 4, then:
make restart
make seed-million                          # 1M users, ~2.5 min
make db-bench                              # server-side read latency
tools/bench/scenario_b.sh 5000 10000 20000 # poll vs push, one batch
tools/bench/scenario_d.sh 10000            # reconnect storm, both read paths
tools/bench/scenario_e.sh 2000             # slow consumers
tools/bench/scenario_f.sh 5000 10000 20000 # celebrity ticker
tools/bench/scenario_i.sh 10000            # Redis engine vs NATS broker
NODES=3 tools/bench/scenario_f_scale.sh 50000 6
```

Every harness refuses to run on live prices (the first Scenario B ladder
crossed 16:00 ET and measured a frozen market), rebuilds the generator image
first, asserts the service under test is configured as the results will
claim, and exits non-zero on a silent run. The load generator is a separate Go
program that verifies its user-id range against the API before generating a
single request. Compare numbers only within a batch.

---

## Before you submit

```bash
# .env: PRICE_SOURCE=api, TRANSPORT=push, BROKER=redis, UVICORN_ARGS= (empty)
make restart
make submit           # runs submit-check, then packages solution.zip
```

`submit-check` refuses a `.env` in benchmark mode because `.env` ships in the
zip. `db/demo.sql` ships too and seeds a reviewer's first start; regenerate it
with `make db-dump` against a stack with no load-test users if the demo state
changes.

The review that preceded phase 3 — what it found, what was fixed, what was
deferred with a reason — is [`docs/review-before-phase3.md`](docs/review-before-phase3.md).
Its recurring lesson is the one this project would pass on: a number is not
evidence until the thing it summarises has been looked at directly.
