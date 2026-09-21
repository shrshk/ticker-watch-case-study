# Review before phase 3

A cold re-read of every file after phases 1 and 2, done before building push so
the comparison in phase 3 is against a baseline that means something. Findings
are ordered by what they did to the numbers, then by severity. Each has a
status.

Legend: **fixed** · **deferred** (with a reason) · **kept** (a choice, stated)

---

## Critical — these distorted the phase 2 measurements

### C1 · The read path wrote to Postgres on every poll — fixed

`auth_controller.default_watchlist_id` was a single
`INSERT … ON CONFLICT (user_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id`,
called by `WatchlistView._resolve_watchlist` on every `GET /watchlist`. Postgres
executes that UPDATE even when the value is unchanged: a new tuple version, a
WAL record, a row lock. `pg_stat_user_tables` showed **8,827,489 `n_tup_upd` on
`watchlists`**, one per poll.

Every phase 2 load figure carried this — the 1.35ms/req CPU, the DB's 178–289%,
and therefore the ~25,000-client ceiling was understated. The README said "two
Postgres round trips"; it was three, one a write.

Fix: `SELECT` first; the insert runs only on a miss. Pinned by
`tests/test_review_regressions.py::TestC1ReadPathDoesNotWrite`, which checks
`pg_stat_xact_user_tables` inside the test's own transaction. The ladder below
was re-run with `n_tup_upd` sampled before and after.

### C2 · The load generator assumed user ids were contiguous from 3 — fixed

`pollClient` minted tokens for `3 + rng.Intn(logicalUsers)`. After the reseed
(`--truncate` deletes and re-inserts), load users occupy ids 1,000,004–2,000,003
and ids 3–1,000,002 no longer exist. Every request would have returned 401
"user no longer exists". Preflight only tested user 1 — a demo user — so it
would have passed, and the run would have reported 100% non-200 responses.

No phase 2 number is affected: no load ran after the reseed. But Scenario B
would have been impossible.

Fix: `-user-id-min` / `-user-id-max` are required flags; preflight samples 25
random ids across the range and refuses to run unless ≥90% return 200. The
three bench runners read the live range from Postgres and pass it.

### C3 · `make restart` did not apply `.env` changes — fixed, then fixed again

`docker compose restart` reuses each container's existing configuration. Editing
`PRICE_SOURCE` or `UVICORN_ARGS` and running `make restart` changed nothing, and
`docs/measurements.md` told the reader to do exactly that. First fix: `docker
compose up -d`. That was still wrong for a *code* change: `up -d` recreates
only on a config change, so with `--workers 4` (no `--reload`) an edited
file under the bind mount kept the old process running while the healthcheck
said healthy - which is exactly how the refresh-token smoke test first ran
against code that did not have refresh tokens. Now `up -d --force-recreate`:
slower, always right.

### C4 · A per-request database lookup on a stateless token — fixed, trade closed

`current_user` verified the JWT and then ran `SELECT … FROM users WHERE id = $1`
on every request to catch "user no longer exists" — the cost of stateful auth
with the guarantees of stateless. The caller now comes from the token's claims
(`Principal`), and `/auth/me` is the one endpoint that consults the database.

The trade this opened - a revoked user's token staying valid until it expired,
24h at the time - is closed by the standard pattern: **15-minute access tokens
and 30-day refresh tokens**, opaque, stored hashed, rotated on every use, with
reuse of a rotated token revoking every session the user holds. Deleting a user
cascades their refresh tokens away, so the session ends within one access-token
TTL. One database read per user per 15 minutes instead of one per poll.
`tests/test_refresh_tokens.py` pins all of it.

---

## High — real bugs, not yet triggered in a run

| | finding | status |
|---|---|---|
| **H1** | `isoformat()` omits fractional seconds when `microsecond == 0`, and the Redis guard compares timestamps as strings. `"…20Z" > "…20.999999Z"` because `Z` > `.`, so a write at exactly `.000000` would beat a newer one from the same second. ~1 in 10⁶ per write × 98/tick ≈ once per ~14h. Two duplicated helpers. | **fixed** — one `shared/timeutil.to_iso` with `timespec="microseconds"`; pinned by `TestH1TimestampOrdering` |
| **H2** | `_write_postgres` / `_write_cache` caught `OSError` only. asyncpg raises `PostgresError`/`InterfaceError`; redis raises `RedisError` subclasses that are not `OSError`. They escaped to the loop's catch-all as "tick failed"; `postgres_errors` never incremented; the connection-exhaustion incident was counted as generic failures. | **fixed** — driver exception types, plus a `cache_errors` counter |
| **H3** | Client never logged out on 401. An expired or invalidated token meant polling every 5s with a dead token, forever, showing "offline". | **fixed** — `api.js` clears the session on 401 and dispatches an event; `App` drops the session |
| **H4** | `${ALBERT_API_KEY:?…}` was required even for `PRICE_SOURCE=simulated`. A reviewer without a key could not run the stack at all. | **fixed** — optional; `AlbertSource` fails loudly when the key is actually needed |
| **H5** | `price-service` had no healthcheck. `up --wait` reported it healthy while it was refusing to start in the `SourceMismatch` backoff loop. | **fixed** — the loop touches `/tmp/heartbeat` each tick; the healthcheck requires it to be under 30s old. *Follow-up:* the refuse-to-start itself was then removed. Only api-over-simulated was ever dangerous; the service now wipes in that direction and adopts real rows in the other (`prices_handler.reconcile_source`, six tests). The 30s backoff loop that looked like a slow start is gone with it. |

---

## Medium — design and measurement validity

| | finding | status |
|---|---|---|
| **M1** | Absolute throughput drifted across the session (4,782 → 3,810–4,011 req/s, same config), attributed to thermal without evidence. Combined with C1 the absolute ceiling was untrustworthy. | **addressed** — re-measured cold in one batch after C1/C4 (table below); docs say to compare only within a batch |
| **M2** | `SIM_SEED` reproducibility holds only from an empty `latest_prices`; a restart mid-session walks on from wherever prices were. The plan's `bench-*` targets that force a clean start were in `.PHONY` and nowhere else. | **deferred** to phase 3, where Scenario B needs it; `.PHONY` corrected |
| **M3** | `round(moved, 2)` with 0.2% volatility freezes sub-$5 tickers (ACB at $3.87: σ ≈ 0.8¢, most moves round to zero). Effective change ratio is below the configured 30% for the tail. | **kept** — cosmetic for the product; noted for phase 3 if the celebrity ticker is ever a cheap one |
| **M4** | 98 sequential `EVALSHA` round trips per tick (~30ms). Wrong shape for phase 3, where the loop gains a publish per ticker. | **fixed** — one pipelined round trip. The first attempt queued nothing (redis-py's async `Script.__call__` must be awaited even against a pipeline); the regression test caught it before it shipped |
| **M5** | Dead or half-built code: `watched_security_ids` (plan §7, mooted by the 99-ticker vendor); `upsert_many` returning `len(rows) if result is None else len(rows)`; `is_synthetic` with nothing honouring it. | **fixed** for the first two; `is_synthetic` **kept** as a column with a comment — it is the hook for the catalog-padding experiment that has not been run |
| **M6** | `pg_trgm` GIN indexes on a 99-row table are inert (seq scan wins; trigrams need ≥3 chars so `NV` cannot use them). README implied they did work. | **fixed** — README says what they are for and that they do nothing today |
| **M7** | Browser and generator polled differently: the client fired twice in the first interval and scheduled from completion (drifting by request latency); the Go generator uses a fixed `time.Ticker`. | **fixed** — client schedules from the previous *start*, one poll per interval |
| **M8** | `deps.connection` holds a Postgres pool connection across the Redis `MGET`. Under a Redis stall the DB pool drains while waiting on Redis. | **deferred** — acquire-per-query is a larger refactor of the handler layer; noted as a phase 4 hardening item |
| **M9** | Redis has no `maxmemory` or eviction policy. Fine as a 98-key cache; not fine once it is Centrifugo's engine. | **deferred** to phase 3 where it becomes relevant |

---

## Low — hygiene

| finding | status |
|---|---|
| Dockerfile pins `uv:0.5.11` against local 0.11.24 — lockfile format drift risk | deferred |
| Containers run as root | deferred (case study) |
| `POSTGRES_DSN` / `REDIS_URL` in `.env.example` are dead — compose hardcodes them | deferred, harmless |
| Default `JWT_SECRET` is 21 bytes; HS256 wants ≥32 (the warning in test output) | deferred |
| `api` had no `restart:` policy while `price-service` did | fixed |
| Generator's token `username` claim was `load_user_{id}`, not the real username | fixed (`user-{id}`, informational) |
| `make up` cold → price-service gives up after 60s and restart-loops | kept — `make bootstrap` is the documented path |
| `_` / `%` in search input act as LIKE wildcards | deferred, harmless at 99 rows |
| `SnapshotReader` under `LATEST_PRICE_SOURCE=postgres` reports everything as a "miss" though the cache was never consulted | deferred, cosmetic |
| `.PHONY` listed `bench-transport` / `bench-broker` that do not exist | fixed |

---

## Harness failures found while re-measuring

Two more, both of the same kind as the ones above — the artifact under test was
not the one the results claimed.

- **`worker_scaling.sh` never rebuilt the load-generator image.** The first
  re-measure ran a stale binary that lacked the new `-user-id-*` flags, failed
  on every run, and printed four rows of dashes with no error, because stderr
  went to `/dev/null` and the exit code was discarded. Both harnesses now
  build the image first and exit non-zero, printing the generator's stderr,
  when a run produces no `request rate` line.
- **The pipelined cache write queued nothing** (M4 above). Caught by the test
  written for it, before the stack was restarted onto it.
- **A stale anonymous volume hid a new dependency** (phase 3). `centrifuge` was
  added to `package.json`, the client image rebuilt with it, the container
  force-recreated - and Vite still returned `Failed to resolve import
  "centrifuge"`. Compose preserves anonymous volumes across recreate unless
  `--renew-anon-volumes`, so `/app/node_modules` from the *old* image was
  still mounted over the new one. `make restart` now renews them.

---

## The plan document — sections overtaken by findings

Corrected in place in `plans/ticker-watch-plan.md`, marked with the date:

| § | said | reality |
|---|---|---|
| 7 | poll the union of watchlisted tickers | moot — one call covers all 99 |
| 7 | `SIM_TICKER_COUNT` | never built; catalog is fixed at 99 |
| 11 | ~2k publishes/sec motivates the broker experiment | **~6/sec** (30 changes per 5s tick). Off by 300x. Redis-as-broker contention may not appear at this scale; phase 3 should measure and be ready to report that it did not |
| 12 | NVDA 350k watchers; celebrity "solved by construction" | 958k at 1M users; solved for publish, unproven for fanout — one move is ~25k socket writes in one channel. Channel sharding is a pre-planned mitigation, built only if measured |
| 16 | ~10k real symbols | 99 |
| 18 | hostile `ulimit -n 256` | this machine ships 1,048,576; the real limit was 16,384 ephemeral ports |
| 19 | Scenario A "10k tickers" | 99 |

---

## The recurring failure, named

Five separate times an aggregate agreed with expectation and the thing
underneath it was never inspected:

1. The Zipf *shape* was right while the hot tickers were alphabetical.
2. API CPU at 500% looked like saturation while Postgres was rejecting
   connections.
3. Three benchmark runs agreed with each other while measuring a
   silently-recreated single-worker server.
4. "Two Postgres round trips" was written without counting; it was three, one
   a write.
5. Four benchmark rows of dashes were produced by a stale image and an
   error-swallowing harness.

The common shape: trusting a summary statistic as evidence about the rows or
processes beneath it. Phase 3 has more of exactly this — fanout counts,
subscriber counts, broker throughput — so the rule going in is: **a number is
not evidence until the thing it summarises has been looked at directly.**

---

## Re-measured baseline after C1 and C4

4 uvicorn workers, generator in a container, 45s runs, one batch, after C1 and
C4. `pg_stat_user_tables.n_tup_upd` on `watchlists` sampled before and after.

| clients | req/s | p50 | p95 | p99 | errors | API CPU | DB CPU |
|---|---|---|---|---|---|---|---|
| 16,000 | 3,018 | 2.0ms | 7.3ms | 21.1ms | 0 | 234% | 79% |
| 20,000 | 3,772 | 3.8ms | 15.2ms | 27.7ms | 0 | 280% | 95% |
| 25,000 | 4,712 | 4.6ms | 19.3ms | 36.5ms | 0 | 343% | 119% |
| 27,500 | 5,182 | 7.8ms | 45.1ms | 85.0ms | 0 | 392% | 134% |
| **30,000** | **5,653** | **5.5ms** | **31.5ms** | **65.7ms** | **0** | **382%** | **155%** |
| 32,500 | 4,433 | 69.4ms | 11,256ms | 17,964ms | 87 | 408% | 157% |

**`n_tup_upd` on `watchlists`: 8,827,826 before, 8,827,826 after.** Roughly
200,000 polls in the batch and not one write - C1 is fixed in the running
system, not just in the test.

Against the pre-fix batch at the same loads:

| clients | req/s before → after | p99 before → after | DB CPU before → after |
|---|---|---|---|
| 16,000 | 2,992 → 3,018 | 26.8ms → 21.1ms | 137% → **79%** |
| 20,000 | 3,743 → 3,772 | 121.4ms → 27.7ms | 178% → **95%** |
| 25,000 | ~3,900 (drifted) → 4,712 | 9–12s → **36.5ms** | ~215% → **119%** |
| 27,500 | **collapsed** (855ms p50, 263 errors) → 5,182 | 19,281ms → **85ms** | 282% → **134%** |

DB CPU roughly halved at every load. Per-request CPU across API and Postgres
fell from ~1.35ms to **~1.02ms**. And 27,500 clients, which collapsed before,
now runs clean with p99 under 100ms - and the knee is now between 30,000 and 32,500 - 30,000 runs
clean at 5,653 req/s with p99 66ms; 32,500 collapses.

The *shape* of every earlier conclusion survives: collapse is still congestive,
update latency is still `interval/2`, the server still responds to request rate
rather than interval. What changed is the absolute ceiling, which was being
held down by a write that should never have been there.

One detail from verifying the client fix in the browser: polls now land at
4956 / 4999 / 4999 / 5001ms regardless of request duration (M7). The first two
requests are 5ms apart - that is React StrictMode's development-only double
mount calling `refresh()` twice, and it does not occur in a production build.
Recorded rather than claimed away.

---

## Before submission — after phase 3

**Ship a small demo database, and load it on first start.**

The original scaffold's `make submit` help text reads "Dump the Postgres
database and package…" but its recipe only zips the directory, and the data
lived in a named volume outside it - an intent that was never implemented. We
will implement the sensible version of it, deliberately *not* the literal one:
the current volume holds 1M load-test users and 9.7M watchlist rows, and a
gigabyte of benchmark data does not belong in a submission.

Design:

- `make db-dump` → `db/demo.sql`, committed. Schema plus demo state only: the
  99 securities, `latest_prices`, `user1`/`user2` and their watchlists, and no
  `load_user_*` rows. A `pg_dump` with the load users excluded, or a small
  script that selects what to keep.
- `make db-restore` to load it explicitly.
- On first start against an **empty** volume, `make bootstrap` / `make up`
  loads `db/demo.sql` instead of running migrations against nothing - so a
  reviewer's first `make open-app` shows a populated watchlist rather than an
  empty one. Postgres's own `/docker-entrypoint-initdb.d/` hook is the
  natural place: it runs only when the data directory is empty, which is
  exactly the semantics wanted, and it costs no application code.
- Migrations stay the source of truth for the schema; the dump must be
  regenerated from them, never hand-edited. `make db-dump` should refuse if
  load users are present, the same way `submit-check` refuses a benchmark
  `.env`.

Why: it demonstrates the "persisted and re-used across restarts" requirement
concretely on the reviewer's own machine, and it is what the scaffold's author
evidently meant. Status: **deferred to after phase 3**, then done before
`make submit`.

---

**Consolidate the documentation, and expose the metrics behind it.**

What exists is accurate but accreted: the README carries the narrative, the
measurements live in `docs/measurements.md` with a corrections banner and a
§2b that supersedes §2, this review holds the findings, and the plan has dated
strike-throughs. A reviewer should not need to read four files to know what
was built, what was measured and what was assumed. Before `make submit`:

- **README as the single arc** (plan §27): what this is → requirements →
  version 1 polling → where it broke, measured → version 2 push → what push
  cost → separation of concerns → channel design → the snapshot path → the
  broker experiment → what was deliberately not built → environment caveats →
  measured results. Rewritten as one pass, not patched.
- **An assumptions register**, explicit and numbered: 99 tickers not 10k; one
  vendor call covers the universe; `effective_at` is observation time because
  the vendor gives none; 30% change ratio (observed 25–45% live); Zipf s=1.1
  with an explicit popularity ranking; Docker Desktop's VM and port forwarding
  as measurement environment; thermal drift across a long session; the
  stateless-token trade closed by 15-minute refresh. Each with where it came
  from and what it would change if wrong.
- **An architecture section with a diagram** — components, the two paths
  (realtime and snapshot), the three delivery guarantees, and which store is
  authoritative for what.
- **A `/metrics` endpoint on both services** (plan §20, phase 4) so every
  number the documents quote can be re-derived live rather than trusted:
  `PriceService.stats` (ticks, upstream calls, changed, cache writes/rejects,
  postgres upserts/errors, `tick_duration_seconds`, `source_reconciliation`)
  and API request counts, latency histogram, cache hit/miss. Prometheus text
  format; Grafana is optional on top.
- **Measured results, final**: one table per question in plan §22, taken in a
  single cold batch after phase 3, replacing the accreted sections rather than
  appending to them. The rule stands: no number appears that was not measured
  on this machine.

Status: **deferred to after phase 3**, then done before `make submit`.

---

## Carried out of phase 3

- **Decision for phase 4: the Redis cache on push.** Measured (§8.4, §8.8):
  no snapshot-latency benefit at one request or at a 5,000-request burst; under
  25k *polling* clients it buys p99 3.5x and 17 DB points for ~30 API points;
  under push it is nearly idle, and the broker on the same Redis moves ~6
  msg/s (§8.7). A production version on push could drop the cache and run
  Postgres as the sole snapshot source. Whether the case study *ships* that
  way, or ships both paths with the toggle and this finding, is a call for
  the author, not the measurement.
- **Scenario G (artificially slow Postgres) not run.** The durable write is
  off the delivery path by construction (concurrent with cache and publish);
  the plan asked for it to be shown under a stall. Deferred - low risk, and
  the harness pattern to do it (a `pg_sleep` trigger on `latest_prices`) is
  ten minutes if wanted.
- **Centrifugo's hot-channel knee is between 25k and 50k on this hardware** (§8.10):
  clean at 25k, superlinear by 50k, unmeasurable at 100k because the VM
  saturated. Attributing the 100k result needs a second host for the
  generators. Sharding stays a discussion point, not code.
- **Generator-side disconnect codes are untrusted** (§8.5); harnesses read
  Centrifugo's counters. A small centrifuge-go fix would restore the
  client-side figure; not worth doing until something needs it.

## What phase 3 inherits

- A polling baseline measured without a write on the read path.
- A load generator that verifies its own user-id range before running.
- Harnesses that rebuild what they test and refuse to report silence as data.
- A corrected publish-rate premise (~6/s, not ~2k/s) for the broker experiment.
- A named celebrity ticker (NVDA, ~9.9% of subscriptions) and a pre-planned
  fanout mitigation (channel sharding) with a measured trigger.
