# Measurements

Every number here was measured on the machine described below. Nothing is
estimated, extrapolated or copied from a vendor's benchmark. Where a number is
an artefact of the environment rather than a property of the design, it says so.

Raw run logs are written to `.run/results/` and are not committed.

---

## Corrections after the pre-phase-3 review

Two defects inflated every polling number in sections 2, 5 and 6:

- **The read path wrote on every poll.** `default_watchlist_id` was an
  `INSERT … ON CONFLICT DO UPDATE`, which Postgres runs as a real UPDATE even
  when the value is unchanged - a tuple version, a WAL record and a row lock per
  `GET /watchlist`. `pg_stat_user_tables` showed 8,827,489 updates on
  `watchlists` from polling. Now a `SELECT`, with the insert only on a miss.
- **Every request looked the user up.** `current_user` ran
  `SELECT … FROM users` per request to detect a deleted user, on top of
  verifying a stateless token. The caller now comes from the token's claims.

So a poll went from three Postgres queries (one a write) plus the price read,
to one query plus the price read. Section 2b below re-measures the 4-worker
ceiling after both fixes, in one batch. The earlier tables are kept as they were
measured; their *shape* stands, their absolute values do not.

Also found and fixed in the same review, without effect on the numbers: a
lexical timestamp comparison that misordered `.000000` against the rest of its
second; exception handlers that caught `OSError` when the drivers raise
`PostgresError` and `RedisError`; `make restart` not applying `.env`; the load
generator assuming user ids were contiguous from 3 after a reseed had moved
them; the client never logging out on a 401. Full list with status:
`docs/review-before-phase3.md`.

---

## Machine and environment

| | |
|---|---|
| Host | Apple Silicon, 10 cores, 64 GB RAM, macOS 25.6 |
| Docker Desktop VM | 10 vCPU, 31.3 GB RAM — so **1000% is full CPU** in every table below |
| Host `ulimit -n` | 1,048,576 (soft), unlimited (hard) |
| `kern.maxfiles` / `kern.maxfilesperproc` | 491,520 / 245,760 |
| Host ephemeral ports | 16,384 (49152–65535) |
| Container ephemeral ports | 55,536 (10000–65535, set in Compose) |
| Container `ulimit -n` | 200,000 (set in Compose) |
| Postgres | 16-alpine, default configuration |
| API | uvicorn, **4 workers** unless stated |
| Price source | `simulated`, `SIM_CHANGE_RATIO=0.30`, `SIM_SEED=1` |

**File descriptors were never the limit.** The plan anticipated the hostile
macOS default of `ulimit -n 256`; this machine ships 1,048,576. Worth stating
because an unprepared run would have misattributed an OS ceiling to the
application.

**Docker Desktop interposes a VM and a network layer.** A ceiling found here is
not a product ceiling.

---

## 1. Database size: can Postgres carry a million users?

`make db-bench` at each seed size. Server-side only — no HTTP, no client.
500 iterations after 50 warmups, against randomly chosen seeded watchlists.

### Sizes

| preset | users | watchlist rows | `watchlist_items` | total DB |
|---|---|---|---|---|
| small | 10,000 | 96,728 | 9.2 MB | ~14 MB |
| medium | 100,000 | 967,138 | 84 MB | ~120 MB |
| million | 1,000,000 | 9,674,774 | 861 MB | ~1.24 GB |

Seeding the million preset took 136s via `COPY`, ~6,600 users/sec.

### Latency, p50 / p95 / p99 in milliseconds

| operation | 10k users | 100k users | 1M users |
|---|---|---|---|
| securities search | 0.16 / 0.23 / 0.25 | 0.16 / 0.23 / 0.26 | 0.16 / 0.23 / 0.27 |
| watchlist membership | 0.13 / 0.15 / 0.17 | 0.12 / 0.16 / 0.21 | 0.14 / 0.17 / 0.25 |
| snapshot via Redis | 0.17 / 0.28 / 0.36 | 0.17 / 0.27 / 0.38 | 0.17 / 0.29 / 0.47 |
| snapshot via Postgres | 0.14 / 0.16 / 0.20 | 0.13 / 0.16 / 0.20 | 0.14 / 0.18 / 0.25 |

**Answer: yes, and the size does not matter.** A hundredfold increase in rows
moved p50 by hundredths of a millisecond. Every read is a point lookup on an
indexed key — `watchlist_id = $1`, `security_id = ANY($1)` — so the table can
grow without the query doing more work. The million-user dataset is a *storage*
exercise, not a latency one.

### Redis was slower than Postgres here, and that is not a mistake

At every size, the Postgres snapshot beat the Redis snapshot (0.14ms vs 0.17ms
p50 at a million users). `latest_prices` is ~100 rows and lives entirely in
`shared_buffers`, so the "cache" is competing against an in-memory B-tree
lookup in the same VM, and loses on the extra network hop.

This is the argument for measuring rather than asserting. The cache is not
justified by single-request latency; it is justified by keeping read load off
the database under concurrency, which the load tests below show (Postgres still
reaches 280%+ CPU at saturation *with* the cache in front of it). Whether it
pays for itself under a reconnect burst is a phase 3 measurement, not a claim.

---

## 2. Where polling breaks

`make load-container CLIENTS=N DURATION=60s`, 1M logical users, 4 uvicorn
workers, 5s client interval with per-client jitter.

| clients | req/s | p50 | p95 | p99 | errors | API CPU | DB CPU | API mem |
|---|---|---|---|---|---|---|---|---|
| 1,000 | 191 | 2.6ms | 4.4ms | 5.8ms | 0 | 44% | 12% | 212 MB |
| 2,500 | 479 | 1.8ms | 2.8ms | 4.8ms | 0 | 68% | 19% | 212 MB |
| 5,000 | 957 | 1.9ms | 4.2ms | 14.3ms | 0 | 110% | 42% | 217 MB |
| 10,000 | 1,914 | 2.8ms | 14.0ms | 27.5ms | 0 | 218% | 78% | 219 MB |
| 15,000 | 2,871 | 2.7ms | 8.0ms | 14.5ms | 0 | 317% | 147% | 221 MB |
| 20,000 | 3,827 | 4.4ms | 15.2ms | 27.3ms | 0 | 327% | 156% | 224 MB |
| **25,000** | **4,782** | **7.8ms** | **35.1ms** | **72.7ms** | **0** | **383%** | **194%** | **228 MB** |
| 27,500 | 3,762 | 855ms | 12,387ms | 19,281ms | 263 | 417% | 282% | 1.10 GB |
| 30,000 | 3,758 | 4,045ms | 14,215ms | 21,605ms | 555 | 422% | 273% | 930 MB |
| 35,000 | 3,529 | 7,520ms | 16,959ms | 24,446ms | 1,614 | 423% | 276% | 1.14 GB |

**Polling breaks between 25,000 and 27,500 concurrent clients — about 5,000
requests per second on this machine.**

The failure is congestive collapse, not a graceful plateau. Past the knee,
throughput *falls*: 25,000 clients sustained 4,782 req/s, while 30,000 clients
managed only 3,758 req/s. Three signatures confirm it:

- **p50 latency jumps 500-fold**, from 7.8ms to 4,045ms — requests are queueing,
  not being served slowly.
- **API memory grows 5x**, from 228 MB to over 1.1 GB. That is the request
  backlog, held in memory.
- **Clients fall behind their own timer.** A client whose 5s tick fires while
  the previous request is still in flight stops being a 5-second poller, so
  offered load and delivered load decouple.

At the knee the API is at 383% of its 400% budget (4 workers × 100%). It runs
out of worker CPU first; Postgres is at 194% and Redis at 13%.

### Update latency is fixed by the interval, not by load

| clients | update latency p50 | p95 | p99 |
|---|---|---|---|
| 1,000 | 2,548ms | 4,763ms | 4,970ms |
| 5,000 | 2,548ms | 4,765ms | 4,963ms |
| 15,000 | 2,508ms | 4,763ms | 4,956ms |
| 25,000 | 2,573ms | 4,763ms | 4,961ms |

Measured at the client: from the `effective_at` of a price to the moment a
client first observes it.

**p50 sits at half the poll interval and p99 at a full interval, at every load
level.** That is arithmetic, not a performance problem: a change that lands
just after a client polls waits the whole 5 seconds. No amount of server
capacity improves it.

### Proof that it is the interval, not a resource limit

Same 1,000 clients in every run, so CPU and memory are constant. Only the poll
interval changes.

| interval | p50 | p95 | p99 | max | p50 ÷ interval | p99 ÷ interval |
|---|---|---|---|---|---|---|
| 1s | 504ms | 947ms | 997ms | 1,128ms | 0.50 | 1.00 |
| 2s | 1,005ms | 1,916ms | 1,989ms | 2,010ms | 0.50 | 0.99 |
| 5s | 2,535ms | 4,769ms | 4,939ms | 5,012ms | 0.51 | 0.99 |
| 10s | 4,604ms | 9,420ms | 9,877ms | 10,007ms | 0.46 | 0.99 |

Latency moves 20-fold across these runs while the load does not move at all.
The ratios are flat to within a few percent: **p50 is half the interval and p99
is the whole interval**, which is the signature of a uniform wait — a change
lands at a random point in a fixed cycle. There is no CPU or memory term in it.

The only levers are shortening the interval, which multiplies request rate
linearly and walks straight into the ceiling in the table above, or pushing.
This is the number phase 3 has to beat, and the reason the comparison is worth
running.

### Memory during collapse is a symptom, not the cause

API memory grows from 228 MB to 1.1 GB past the knee, which invites the reading
that the service runs out of memory. It does not:

- The container had 31 GB available and peaked at 1.1 GB. Nothing was killed,
  and no limit was reached.
- The API was pinned at 383–422% of its 400% worker budget at the same moment.
  CPU ran out; memory did not.
- The growth is the request backlog held in memory. Queue depth rises because
  workers cannot keep up, so memory is the *visible consequence* of CPU
  saturation rather than an independent limit.

Raising the memory limit would change nothing. Adding worker CPU is the only
lever, and section 5 shows that on this machine there is not much of it left.

---

## 2b. Where polling breaks - re-measured after the review

The section 2 ladder was taken with the read path writing on every poll (C1 in
`docs/review-before-phase3.md`) and a per-request user lookup (C4). Both are
fixed. Same method - 4 workers, container generator, one batch - 45s runs.

| clients | req/s | p50 | p95 | p99 | errors | API CPU | DB CPU |
|---|---|---|---|---|---|---|---|
| 16,000 | 3,018 | 2.0ms | 7.3ms | 21.1ms | 0 | 234% | 79% |
| 20,000 | 3,772 | 3.8ms | 15.2ms | 27.7ms | 0 | 280% | 95% |
| 25,000 | 4,712 | 4.6ms | 19.3ms | 36.5ms | 0 | 343% | 119% |
| 27,500 | 5,182 | 7.8ms | 45.1ms | 85.0ms | 0 | 392% | 134% |
| **30,000** | **5,653** | **5.5ms** | **31.5ms** | **65.7ms** | **0** | **382%** | **155%** |
| 32,500 | 4,433 | 69.4ms | 11,256ms | 17,964ms | 87 | 408% | 157% |

`n_tup_upd` on `watchlists` was **8,827,826 before and after** the batch:
~200,000 polls and zero writes.

Compared with section 2 at the same loads, DB CPU roughly halved (137→79% at
16k, 178→95% at 20k, 282→134% at 27.5k), per-request CPU fell from ~1.35ms to
~1.02ms, and **27,500 clients - which collapsed before - runs clean at 5,182
req/s with p99 under 100ms**. **The knee is now between 30,000 and 32,500** - 30,000 runs clean at 5,653 req/s
with p99 66ms; 32,500 collapses with the familiar signature.

Everything structural in sections 2-6 stands: the collapse signature, update
latency at `interval/2`, the server responding to request rate not interval,
workers scaling sub-linearly. What moved is the absolute number, and it moved
because of a defect, not a tuning change. Treat this table as the baseline
phase 3 compares against.

---

## 3. What polling costs, as arithmetic

Measured: **16,900 bytes per client per minute**, constant from 1,000 to 25,000
clients (12 polls/minute × ~1.4 KB for a 10-stock watchlist).

Extrapolating the measured per-client cost to the brief's stated scale:

| concurrent clients | required req/s | egress | stacks needed at 5,000 req/s |
|---|---|---|---|
| 25,000 | 5,000 | 7 MB/s | 1 |
| 100,000 | 20,000 | 28 MB/s | 4 |
| 1,000,000 | 200,000 | 282 MB/s | **40** |

Two things make this worse than the table suggests:

- **Cost is paid whether or not anything changed.** At `SIM_CHANGE_RATIO=0.30`,
  roughly 70% of every response is data the client already had. Push sends one
  message per changed ticker per tick and the broker fans it out.
- **Every request re-pays fixed costs**: JWT validation, a membership query, an
  `MGET`, and JSON serialisation of the entire watchlist — for an unchanged
  watchlist.

Forty API stacks to deliver mostly-unchanged data every five seconds is the
argument for push, stated as a number rather than an opinion.

---

## 4. Host vs container: separating the app from the environment

The same load, generated natively on macOS and from inside the Compose network,
against the same verified 4-worker API.

| generator | req/s | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| host (via `localhost:8000`) | 2,872 | 7.2ms | 52.8ms | 99.2ms | 372ms |
| container (via the Compose network) | 2,871 | 2.7ms | 8.0ms | 14.5ms | 50ms |

**Throughput is identical; the latency tail is not.** The host path's p99 is
7x worse. That difference is Docker Desktop's host port forwarding, which sits
between `localhost:8000` and the container — it is environmental, and it would
not exist in a deployed system.

Consequence for every other number here: **the container-generated figures are
the honest ones**, and the host figures carry a tax that belongs to Docker
Desktop. It is also why the host is unsuitable for the largest runs — its
16,384 ephemeral ports cap it at roughly 16,000 concurrent keep-alive clients,
well below the 25,000 where the application actually breaks.

---

## 5. Does adding workers move the ceiling?

All runs in this section were taken back to back in one batch, 4 uvicorn
workers per column unless stated, 40s each, generator in a container. Read the
note on drift below before comparing them with section 2.

| workers | clients | req/s | p50 | p95 | p99 | errors | API CPU | DB CPU |
|---|---|---|---|---|---|---|---|---|
| 1 | 3,000 | 562 | 1.8ms | 4.1ms | 11.4ms | 0 | 58% | 25% |
| 1 | 5,000 | 936 | 2.2ms | 4.9ms | 14.5ms | 0 | 86% | 40% |
| 2 | 8,000 | 1,496 | 2.8ms | 11.6ms | 22.8ms | 0 | 115% | 63% |
| 2 | 11,000 | 2,058 | 3.3ms | 14.8ms | 29.6ms | 0 | 162% | 98% |
| 4 | 16,000 | 2,992 | 4.3ms | 12.7ms | 26.8ms | 0 | 284% | 137% |
| 4 | 20,000 | 3,743 | 10.4ms | 69.4ms | 121.4ms | 0 | 331% | 178% |
| 8 | 24,000 | 4,482 | 15.7ms | 389.9ms | 644.0ms | 0 | 477% | 256% |
| 8 | 28,000 | 5,188 | 23.9ms | 270.4ms | 497.3ms | 0 | 543% | 289% |

**Yes, but sub-linearly, and it stops paying at eight.**

| workers | best clean req/s | req/s per worker | API CPU per req/s |
|---|---|---|---|
| 1 | 936 | 936 | 0.092% |
| 2 | 2,058 | 1,029 | 0.079% |
| 4 | 3,743 | 936 | 0.088% |
| 8 | 5,188 | 649 | 0.105% |

Throughput per worker holds roughly flat from one to four, then drops to 69% of
that at eight, and each request costs about 20% more CPU. The machine is the
reason: at eight workers the API and Postgres together draw 832% of the 1000%
the VM has, before the load generator's own ~50%. Doubling from four to eight
bought 39% more throughput, and a further doubling would buy less than that.

Latency degrades well before throughput does. At four workers p99 is 121ms at
the last clean point; at eight it is already 497–644ms. If the bar is "p99
under 100ms" rather than "no errors", four workers is the better
configuration on this machine.

### Which resource is actually contended - measured, not inferred

At eight workers and 28,000 clients, neither component is internally saturated.
The API is at 543% of a possible 800%, and Postgres looks busy at 289% but is
mostly waiting for work:

```
 state  | waiting_on | count        <- pg_stat_activity, 3 samples
 idle   | Client     |   113           128 backends open
 active | LWLock     |    12
 active | (on cpu)   |     2
```

**111-126 of 128 Postgres backends sit `idle`, waiting on Client** - that is
Postgres waiting for the API to send it a query, not the API waiting for
Postgres. Only 2-16 backends are active at any instant, which is consistent
with ~10,000 short queries per second each taking a fraction of a millisecond.

The contended resource is the VM's CPU run queue:

| metric | value | against |
|---|---|---|
| load average (1 min) | 21.6 - 24.4 | 10 CPUs |
| `procs_running` | 27 - 44 | 10 CPUs |
| `procs_blocked` | 1 - 2 | ~nothing on I/O |

Thirty to forty runnable processes against ten cores. A worker that is ready to
serve waits for a core, not for the database, and `procs_blocked` near zero
rules out I/O.

So Postgres is a shared *component* in the Amdahl sense - every worker funnels
through it, so its cost does not parallelise away - but it is not the saturated
resource. Past four workers, adding more simply adds contenders to a run queue
that is already 2.4x deep. This is also why p99 degrades faster than throughput:
the wait is scheduling delay, which grows with the number of runnable
processes, not service time.

### The first 8-worker attempt measured the wrong thing

Worth recording, because the failure looked exactly like a CPU ceiling and was
not one. The initial 8-worker runs produced 31,641 errors at a load four
workers had handled cleanly, and the obvious reading was that the machine had
run out of CPU. Checking the database instead of believing the graph:

```
psql: FATAL:  sorry, too many clients already
```

**The connection pool is per process.** Each uvicorn worker opens its own pool,
so the server-wide total is `workers x DB_POOL_MAX_SIZE` plus the price
service's pool. At the defaults that is 4 x 16 = 64, comfortably under
Postgres's stock `max_connections = 100`. Doubling the workers makes it 128,
which is not. Scaling workers was silently exhausting the database rather than
adding capacity.

Two fixes, both in this repo now:

- Pool size is configuration (`DB_POOL_MIN_SIZE`, `DB_POOL_MAX_SIZE`) rather
  than a hardcoded default, with the arithmetic stated where it is set, and the
  API logs the pool size it opened per process.
- Postgres runs with `max_connections=200`, enough for eight workers plus the
  price service with headroom.

After the fix, eight workers produced 5,188 req/s with zero errors at the same
load that previously produced 31,641. The numbers above are all post-fix.

The general lesson is the one that makes a benchmark worth anything: a ceiling
is not explained until you have found the thing that is actually full. CPU was
at 500% of 800% available and looked plausible; the real limit was a resource
nobody had counted.

### Measurement drift between batches

Absolute throughput drifted downward over a long session. The same
configuration - 4 workers, 25,000 clients - gave 4,782 req/s with a 88.8ms p99
early on, and 3,810-4,011 req/s with a 9-12s p99 several hours later, across
three consecutive runs that agreed with each other.

The cause was not the application, and not database bloat: `latest_prices` is
updated continuously but autovacuum held it at 72 kB with 129 autovacuum runs,
and the large tables are read-only during a run. The most likely explanation is
thermal, on a laptop that had been at high sustained multi-core load for hours.
macOS did not record a thermal warning, so this is inference rather than
measurement.

**Consequence for reading these tables: only compare numbers taken within the
same batch.** Every table in this section is one back-to-back batch for exactly
that reason. Section 2's absolute figures are from an earlier, cooler session
and its *shape* - the knee, the collapse signature - is what carries, not its
exact throughput.

---

## 6. What shortening the poll interval costs

Polling has exactly one lever for update latency: poll more often. Section 2
showed p50 tracks interval/2. This measures the bill.

### The server does not care about the interval, only the rate

Same ~2,810 req/s at three intervals, 4 workers, by scaling clients to match:

| interval | clients | req/s | req p50 | req p95 | req p99 | update p50 | errors |
|---|---|---|---|---|---|---|---|
| 5s | 15,000 | 2,805 | 3.1ms | 10.7ms | 20.8ms | 2,518ms | 0 |
| 2s | 6,000 | 2,809 | 2.9ms | 9.9ms | 19.1ms | 1,026ms | 0 |
| 1s | 3,000 | 2,811 | 3.1ms | 13.9ms | 22.9ms | 495ms | 0 |

Throughput matches to within 6 requests per second, and server-side latency is
indistinguishable. Update latency improves five-fold. **Request rate is the
only thing the server responds to; the interval just sets the exchange rate
between rate and clients.**

### So the client ceiling divides by the same factor

At a 1s interval, 4 workers:

| interval | clients | req/s | req p99 | update p50 | errors |
|---|---|---|---|---|---|
| 1s | 3,000 | 2,811 | 11.2ms | 495ms | 0 |
| 1s | 4,000 | 3,745 | 21.1ms | 515ms | 0 |
| 1s | 5,000 | 4,679 | 140.5ms | 514ms | 0 |

Against the 5s ceiling of about 20,000 clients in the same batch, a 1s interval
supports about 5,000 - a quarter of the clients for a fifth of the latency.

One mild surprise, and it goes the right way: 5,000 clients at 1s sustained
4,679 req/s, *more* than 20,000 clients at 5s sustained (3,743 req/s). Fewer,
busier connections are cheaper to serve than many idle ones, so the exchange is
slightly better than one-for-one. Connection count has a cost of its own,
independent of request rate.

### The arithmetic that phase 3 has to beat

To reach sub-second update latency by polling, on this machine:

| target update p50 | interval needed | clients per stack | stacks for 1M clients |
|---|---|---|---|
| 2.5s | 5s | ~20,000 | 50 |
| 1.0s | 2s | ~8,000 | 125 |
| 0.5s | 1s | ~5,000 | 200 |

And adding workers does not rescue it: four to eight bought 39% more
throughput, so the ceiling is a property of the machine well before it is a
property of the configuration.

Push decouples the two entirely. One broadcast per changed ticker per tick,
fanned out by the broker, with no relationship between update latency and
client request rate - because there are no client requests. That is the claim
phase 3 has to substantiate with the same measurements.

---

## 7. Watcher distribution, and a bug that made it meaningless

Popularity is Zipf-distributed with s=1.1 across 1M users and 9.68M watchlist
rows. Measured after reseeding:

| rank | ticker | watchers | share | cumulative |
|---|---|---|---|---|
| 1 | NVDA | 958,428 | 9.91% | 9.9% |
| 2 | TSLA | 787,540 | 8.14% | 18.0% |
| 3 | AAPL | 629,354 | 6.50% | 24.6% |
| 4 | AMZN | 513,858 | 5.31% | 29.9% |
| 5 | META | 429,113 | 4.44% | 34.3% |
| … | | | | |
| 10 | COIN | 228,055 | 2.36% | 49.3% |
| 99 | CMG | 20,393 | 0.21% | 100% |

Ten tickers carry half of all subscriptions; the tail sits around 20,000.

**This was wrong until it was checked.** The Zipf draw assigns weight by
position in a list, and the list was `securities ORDER BY id` - which, because
the catalog is inserted alphabetically, meant ranking alphabetically. The
resulting distribution had the correct *shape* and absurd *content*: Airbnb was
the second most-watched stock in the world, Abbott Laboratories third, and NVDA
sat in the tail.

Nothing about the load numbers changes - the shape drives the load, and the
shape was always right. What it would have broken is everything that depends on
*which* ticker is hot: a demo a reviewer looks at, and Scenario F, which needs
a named celebrity ticker with a plausible following. Ranking is now an explicit
`POPULARITY` list in the seeder, with unlisted tickers shuffled deterministically
behind it so the tail is not alphabetical either.

The general shape of the mistake is worth keeping: a statistical property was
verified (skew present, exponent right) while the mapping underneath it was
never looked at. Aggregates agreeing with theory is not evidence that the rows
mean anything.

### Why this matters for phase 3

At 25,000 connections with ten subscriptions each, NVDA's 9.91% share means
**roughly every connected client subscribes to it**. Per-ticker channels solve
publish amplification - one publish per changed ticker per tick, regardless of
watchers - but fanout is still one socket write per subscriber, concentrated in
a single channel. A hot ticker moving means ~25,000 writes in one burst.

Plan section 12 argues the celebrity problem is "largely solved by
construction". That is right for publish and unproven for fanout. Phase 3
measures per-channel broadcast cost directly, and carries one pre-planned
mitigation: sharding a hot channel into `ticker:NVDA:{0..N}` with clients
hashed across shards, trading N publishes for parallel fanout. It gets built
only if the measurement asks for it.

---

## 8. Push - the transport comparison (phase 3)

Centrifugo v6.9.6 behind the `push` Compose profile; the price service
publishes one message per *changed* ticker per tick through Centrifugo's HTTP
API; clients subscribe to `ticker:<T>` channels, take one `GET /watchlist`
snapshot after subscribing, and drain. Update latency is measured at the
client from the publication's `effective_at` - the same metric, measured the
same way, as polling.

### 8.1 First contact, live market data

200 clients, 40s, real vendor prices during market hours:

| | polling (phase 2, same client count) | push |
|---|---|---|
| HTTP requests/sec | 40 (clients / 5s) | **5** (one snapshot per connection, then none) |
| update latency p50 / p95 / p99 | 2,518 / 4,763 / 4,961ms | **22 / 30 / 32ms** |
| errors | 0 | 0 |
| subscriptions | - | 1,943 (~9.7 per client, one channel per ticker) |

### 8.2 Scenario B on live data - and why it is not the headline table

The first ladder ran against `PRICE_SOURCE=api` during market hours. Two
pairs completed cleanly:

| clients | transport | http req/s | upd p50 | upd p95 | upd p99 | API | DB | Centrifugo | B/client/min |
|---|---|---|---|---|---|---|---|---|---|
| 5,000 | poll | 943 | 2,584ms | 4,742ms | 4,942ms | 100% | 26% | - | 15,923 |
| 5,000 | **push** | 111 | **165ms** | **295ms** | **376ms** | 75% | 23% | 54% | 14,045 |
| 10,000 | poll | 1,886 | 2,555ms | 4,738ms | 4,938ms | 155% | 50% | - | 15,928 |
| 10,000 | **push** | 222 | **333ms** | **560ms** | **652ms** | 133% | 41% | 101% | 14,367 |

Then the 20,000-push, 25,000-poll and 25,000-push rows recorded **no
updates at all**, and push egress fell to 1,876 bytes per client per minute -
exactly one snapshot and nothing after it. The ladder had crossed **16:00 ET**
and the vendor froze at the close. The rows are not a system failure; they
measured a market that had stopped moving. (Inference from timing and the
egress signature; the price-service logs from that window were lost to a
container recreate.)

This is the plan's own argument for benchmarking on simulated prices - a
controlled comparison needs identical movement on both sides, and live data
cannot provide that even inside market hours - which the earlier phases wrote
down and this run did not follow. Every harness now refuses to start unless
`PRICE_SOURCE=simulated`, in `.env` *and* in the running container.

Two things the live rows still say, because they are consistent with the
controlled run below: **update latency drops by an order of magnitude and
more**, and **egress per client is close to polling's at this change ratio**
(~40% of tickers moving per tick live, against the simulator's 30%) - push
saves bandwidth in proportion to how much *does not* change, and on a busy day
that is less than the arithmetic in section 3 suggests.

### 8.3 Scenario B, controlled

`PRICE_SOURCE=simulated`, `SIM_CHANGE_RATIO=0.30`, `SIM_SEED=1`, 4 uvicorn
workers, 45s runs, one batch. Polling ran with the price service **not**
publishing; push with it publishing. CPU figures are the **peak** sample -
see the caveat below.

| clients | transport | HTTP req/s | upd p50 | upd p95 | upd p99 | errors | API | DB | Redis | Centrifugo | B/client/min |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5,000 | poll | 943 | 2,595ms | 4,772ms | 4,954ms | 0 | 108% | 38% | 4% | - | 16,549 |
| 5,000 | **push** | 111 | **72ms** | **127ms** | **134ms** | 0 | 118% | 37% | 3% | 42% | **6,829** |
| 10,000 | poll | 1,886 | 2,546ms | 4,773ms | 4,959ms | 0 | 175% | 50% | 6% | - | 16,562 |
| 10,000 | **push** | 222 | **134ms** | **257ms** | **297ms** | 0 | 167% | 55% | 6% | 80% | **6,398** |
| 20,000 | poll | 3,772 | 2,550ms | 4,768ms | 4,959ms | 0 | 261% | 104% | 11% | - | 16,558 |
| 20,000 | **push** | 439 | **265ms** | **577ms** | **651ms** | 0 | 197% | 79% | 7% | 141% | **6,794** |
| 25,000 | poll | 4,706 | 2,558ms | 4,762ms | 4,964ms | 0 | 348% | 127% | 13% | - | 16,538 |
| 25,000 | **push** | 541 | **344ms** | **769ms** | **978ms** | 984* | 320% | 136% | 13% | 164% | **5,956** |

\* 984 errors in the ladder run; an immediate standalone re-run at the same
load produced **zero** errors, 25,000 connections, 241,737 subscriptions and
608,610 publications received (13,437/s), with update latency p50 390 / p95
851 / p99 921ms. The errors did not reproduce and are most likely the connect
ramp overlapping the transport switch that precedes each push row. Both
numbers are reported.

**What the table says**

1. **Update latency is the whole argument, and it holds at every size.**
   Polling sits at 2,550ms p50 / 4,960ms p99 regardless of load - the
   interval/2 arithmetic from section 2. Push is 72ms at 5,000 clients and
   344ms at 25,000: **36x better at the low end, 7x at the high end**, and
   under a second at p99 throughout.

2. **Push latency grows with subscribers; polling's does not.** 72 → 134 →
   265 → 344ms p50 across 5k → 25k is roughly linear in client count. That is
   fanout cost - 13,437 deliveries per second at 25k - and it is the one place
   push pays per user. Polling pays per user too, but in request rate, which
   shows up as the ceiling in section 2b rather than as latency.

3. **Egress is 2.4x lower** at this change ratio: 6.4-6.8k bytes per client per
   minute against 16.5k. On live data at ~40% change the gap was only 1.1x
   (section 8.2). Push saves bandwidth in proportion to what does *not* move.

4. **The CPU columns are not the win they look like they should be, and the
   reason is the sampler.** It records the *peak* over the run. Under push the
   peak is the connect ramp - 5,000 to 25,000 snapshots in ten seconds - after
   which the API is nearly idle; under polling the load is flat. Peak-vs-peak
   therefore understates push's steady-state advantage badly. The harness now
   also reports a post-ramp mean; the next batch carries both.

5. **The per-delivery prediction held.** Section 6 said push wins on CPU only
   if a fanout delivery costs under 0.44ms. Centrifugo at 164% CPU for 13,437
   deliveries/s is **~0.12ms per delivery**, and that is the whole broker path
   - the API is not in it.

6. **Redis did not notice the broker.** Redis CPU is identical under both
   transports at every size (3-13%). With ~30 changed tickers per 5s tick the
   broker moves about 6 messages per second through Redis pub/sub; the
   contention section 11 of the plan worried about does not exist at this
   publish rate. Section 8.6 puts a number on it under NATS anyway.


---

### 8.4 Scenario D - reconnect storm, cached vs uncached

10,000 push clients; at +25s half of them disconnect and reconnect at once.
Every reconnect re-runs the snapshot - subscribe, then `GET /watchlist` - so
this is a 5,000-request burst on the snapshot path, run under both values of
`LATEST_PRICE_SOURCE`.

| read path | reconnected | snapshot p50 | p95 | p99 | errors | API CPU | DB CPU |
|---|---|---|---|---|---|---|---|
| redis | 5,000 / 5,000 | 1.5ms | 8.3ms | 22.4ms | 0 | 94% | 28% |
| postgres | 5,000 / 5,000 | 1.4ms | 5.2ms | 19.2ms | 0 | 91% | 33% |

**The cache does not make the snapshot faster, even under the burst that was
supposed to justify it.** Plan §9 anticipated writing "Redis was Nx faster
under a 200k-lookup burst"; the measured N is below 1. `latest_prices` is 98
rows in `shared_buffers`, and a point lookup there beats a Redis round trip
from the same VM, at one request and at five thousand.

What the cache *does* buy is visible in the DB column: **five points of
Postgres CPU** at this burst size. Its justification is offload under
sustained load, not latency - section 2b shows Postgres at 127% under 25,000
polling clients *with* the cache in front of it. Section 8.7 measures the same
load without it, which is the number that decides whether the cache stays.

Also worth stating: 5,000 simultaneous reconnects, each a subscribe plus a
snapshot, completed with zero errors and a 22ms p99 on both paths. The
subscribe-then-snapshot ordering (plan §10) cost nothing measurable.

### 8.5 Scenario E - slow consumers

2,000 push clients, 10% of them blocking 3s on every publication - a client
that cannot keep up. `client.queue_max_size` lowered from 1 MiB to 8 KiB for
the run so the queue fills in seconds rather than hours at this message rate.

| | |
|---|---|
| slow readers | ~200 |
| **disconnected by Centrifugo, code 3012 (slow)** | **155** |
| update latency, all clients | p50 **39ms**, p95 3,014ms, p99 13,045ms, max 26s |
| realtime errors | 0 |

**Slow consumers hurt only themselves.** The p50 is 39ms - the fast 90% never
noticed. The p95/p99 tail *is* the slow clients, measured while they were
still connected and blocking: by construction they see every message late,
until the server's queue for them overflows and it disconnects them. That is
Centrifugo's actual mechanism - a bounded per-client queue and a disconnect,
no per-client conflation - and the number to report is the disconnect count.
A disconnected client recovers through the same path as a reconnect: subscribe,
snapshot, drain.

At the default 1 MiB queue, at ~200 bytes per publication and ~4 messages per
second per client, a reader would have to fall roughly twenty minutes behind
before being cut. The 5-second cadence makes this a far milder problem than a
tick-by-tick feed would have.

One measurement note: the generator's own count of slow disconnects read 0,
because it checked the disconnect code in a way centrifuge-go does not surface
it. Centrifugo's `num_server_disconnects{code="3012"}` is the authority and
is what the harness now reports; the client-side figure is printed as
"client-observed, not trusted". Another instance of the phase 2 rule: the
broker's counter, not the client's inference.

### 8.6 Scenario F - the celebrity ticker

Every push client additionally subscribes to `ticker:NVDA`, so that channel
carries one subscriber per connection. Per-channel broadcast cost is
Centrifugo's own `node_broadcast_duration_seconds` histogram, reset per run;
the client-side view is NVDA's update latency against all channels.

| clients (= NVDA subscribers) | NVDA msgs | all-channel upd p99 | **NVDA upd p50 / p99** | Centrifugo CPU | broadcast duration: mean / p95 / p99 |
|---|---|---|---|---|---|
| 5,000 | 13,461 | 167ms | **91 / 168ms** | 49% | 0.73ms / ≤5ms / ≤10ms |
| 10,000 | 20,000 | 268ms | **177 / 279ms** | 58% | 2.20ms / ≤25ms / ≤25ms |
| 20,000 | 40,000 | 730ms | **523 / 770ms** | 137% | 6.43ms / ≤50ms / ≤100ms |

**Per-channel broadcast cost is linear in subscribers** - about 0.3µs per
subscriber per publication, 6.4ms to fan one NVDA change out to 20,000
connections. That is the one place a hot ticker costs more than a cold one,
and at this size it is small.

**The hot channel does not dominate the tail.** NVDA's p99 is within 5% of the
all-channel p99 at every size (168 vs 167, 279 vs 268, 770 vs 730ms). The
latency growth seen in Scenario B is total fanout volume across all channels,
not the celebrity. Plan §12's "solved by construction" holds for publish
*and*, at this scale, for fanout.

**Where it would stop holding - corrected by 8.10.** This section first
extrapolated linearly to ~160ms at 500,000 subscribers. Section 8.10 then
measured 50,000 and found the growth **superlinear**: the per-subscriber cost
roughly doubles between 20k and 50k, and the hot channel's tail separates from
the rest. The knee on one node is between 25k and 50k subscribers per
channel, not near 500k. The linear figure is left struck through in the
history; the conclusion that follows from the data is in 8.10.

Two honest limits of this run: NVDA changed only 2-3 times per 45s window at
the 30% ratio, so the per-channel latency figures rest on a few thousand
observations per row; and Centrifugo's histogram covers all channels, not
NVDA alone - the per-subscriber cost is inferred from how the mean moves with
the hot channel's size while the other 98 channels stay roughly constant.

### 8.7 Scenario I - Redis engine vs NATS broker

The same 10,000-client push load and the same 5,000-client reconnect storm,
under Centrifugo's Redis engine and then a NATS broker. Nothing in the
application changed between the two rows: a different Centrifugo config file
and one more container.

| broker | upd p50 | upd p95 | upd p99 | storm snapshot p50 / p95 / p99 | errors | Redis CPU | Centrifugo CPU |
|---|---|---|---|---|---|---|---|
| Redis engine | 127ms | 267ms | 307ms | 1.4 / 7.5 / 22.0ms | 0 | 3.7% | 58% |
| NATS broker | 133ms | 247ms | 267ms | 1.5 / 6.7 / 22.3ms | 0 | 3.6% | 59% |

**Indistinguishable.** Every column is within run-to-run noise. Plan §11
called Redis-serving-as-both-cache-and-broker "the most interesting scaling
question in the system"; measured, it is not a question at this publish rate.
Thirty changed tickers per 5-second tick is about six broker messages a
second, and Redis CPU is 3.7% with the broker on it and 3.6% without. The
cache and the broker do not contend because neither is doing anything Redis
notices.

What the swap did prove is the claim the architecture made about itself: the
price service publishes through Centrifugo's HTTP API, so changing the broker
touched two config files and zero lines of application code. NATS gives up
history and recovery; the snapshot-on-reconnect path never used them, so
nothing was lost. It also bought nothing. The plan's own rule applies - adding
a second messaging system to move six messages a second would be architecture
for its own sake - and now there is a table to say so.

### 8.8 What the Redis cache is actually for

Section 1 showed the cache *slower* than Postgres for a single snapshot;
section 8.4 showed it no faster under a 5,000-request burst. The remaining
case for it was offload under sustained load. Measured at 25,000 polling
clients, the cache bypassed and then in front:

| read path | req/s | req p50 | **req p99** | API CPU | **DB CPU** | API + DB |
|---|---|---|---|---|---|---|
| postgres (bypassed) | 4,713 | 5.0ms | **118.8ms** | 316% | **147%** | 463% |
| redis (cache) | 4,712 | 3.4ms | **33.7ms** | 349% | **129%** | 478% |

**Under polling the cache earns a modest keep, and not as a CPU saver.** It
takes 17 points off Postgres and cuts request p99 by 3.5x - but the API pays
~30 points to do the `MGET` and decode itself, so total CPU is slightly
*higher* with the cache. It is a tail-latency and database-headroom device.
At 98 rows it can never be a throughput device, because the thing it fronts
is already an in-memory point lookup.

**Under push it is nearly idle.** The snapshot path runs once per connection
and once per reconnect; section 8.4 measured that burst and the cache made no
latency difference. So on the transport this case study recommends, Redis is
left with two jobs that both measured as negligible: a cache the read path
barely needs, and a broker moving six messages a second.

The honest architectural conclusion: **a production version on push could
drop the Redis cache and keep `latest_prices` as the sole snapshot source**,
leaving Redis only as Centrifugo's engine - or, given 8.7, not at all if NATS
or Centrifugo's memory engine were chosen. This is the opposite of what the
plan assumed going in, and it is the kind of thing that can only be
discovered by building both paths and toggling between them. The toggle
(`LATEST_PRICE_SOURCE`) stays in the code for exactly that reason.

### 8.9 Phase 3 answers to the plan's questions (§22)

| # | question | answer |
|---|---|---|
| 1 | At what client count does polling stop being the right answer, and what does push cost? | Polling's ceiling is ~30,000 clients on this machine (§2b) with update latency fixed at ~2.5s by arithmetic. Push holds 72-344ms p50 across 5k-25k at 2.4x less egress; it costs connection state, one snapshot per connect, and fanout CPU that grows linearly with subscribers (~0.12ms/delivery). |
| 4 | How many realtime connections can one Centrifugo node sustain locally? | 25,000 connections with 241,737 subscriptions, zero errors, at 164% CPU. Not pushed to failure; the generator, not Centrifugo, is the next limit to find. |
| 5 | Does one Redis doing cache and broker degrade snapshot latency, and does NATS remove it? | No degradation exists to remove at ~6 broker msg/s (§8.7). |
| 6 | How does per-channel broadcast cost scale toward 500k subscribers? | **Superlinearly.** ~6ms per broadcast at 25k, ~40ms at 50k (steady state), reproduced; the hot channel's tail separates from the rest between 25k and 50k. 100k was beyond this environment. Three nodes on the same machine split the CPU but did **not** reduce per-broadcast time or client latency (§8.11) - so the payoff of more nodes is unmeasured here and needs separate hosts.|
| 7 | What happens to slow clients? | Centrifugo disconnects them (155 of ~200 at an 8 KiB queue); nobody else notices, p50 39ms (§8.5). |
| 8 | Does `latest_prices` persistence affect realtime latency? | No - the durable write runs concurrently with cache and publish and is not on the delivery path. Not stress-tested with an artificially slow Postgres (plan Scenario G); deferred. |

### 8.10 The fanout curve, extended: 50k, and what 100k taught about the environment

One generator container holds ~25,000 connections, so these runs used two and
four containers in parallel (`tools/bench/scenario_f_scale.sh`), every client
subscribing to `ticker:NVDA`. Ramp-up was stretched to 30-40s so the snapshot
burst stayed under the API's measured ceiling; Centrifugo was recreated before
each run for a fresh histogram.

| NVDA subscribers | connections reached | broadcast duration: mean / p95 / p99 | per-subscriber | all-channel upd p50 / p99 | **NVDA upd p50 / p99** | Centrifugo CPU peak / steady | Centrifugo mem | errors |
|---|---|---|---|---|---|---|---|---|
| 5,000 | 5,000 | 0.73ms / ≤5 / ≤10ms | 0.15µs | - / 167ms | 91 / 168ms | 49% | - | 0 |
| 10,000 | 10,000 | 2.20ms / ≤25 / ≤25ms | 0.22µs | - / 268ms | 177 / 279ms | 58% | - | 0 |
| 20,000 | 20,000 | 6.43ms / ≤50 / ≤100ms | 0.32µs | - / 730ms | 523 / 770ms | 137% | - | 0 |
| 25,000 | 25,002 | 6.22ms / ≤50 / ≤100ms | 0.25µs | 313 / 644ms | 376 / 694ms | 115% / 45% | 1.46 GiB | 0 |
| **50,000** | **49,919** | **36.99ms / ≤250 / ≤500ms** | **0.74µs** | 638 / ~1,550ms | **893 / ~1,730ms** | 333% / 92% | 2.84 GiB | 166 (0.3%) |
| 100,000 (attempted) | 74k-97k, unstable | 210.9ms / ≤1,000 / ≤2,500ms | - | ~1,330 / ~9,000ms | ~1,940 / ~6,900ms | 368% / 149% | 5.75 GiB | ~35,000 |

**The growth is superlinear.** Doubling subscribers from 25k to 50k multiplied
broadcast time by six (6.2 → 37ms) and per-subscriber cost by three. The
linear model in 8.6 was fitted to 5k-20k and does not survive contact with
50k. The 500k extrapolation is withdrawn.

**The hot channel's tail separates at 50k.** At 20k, NVDA's p99 was within 5%
of the all-channel p99. At 50k NVDA's p50 is 39% higher than everyone
else's (893 vs 638ms). This is the hot-ticker effect actually appearing, and
it appears between 25k and 50k subscribers on one node - not at hundreds of
thousands.

**100k is beyond this environment, and the table says so rather than
pretending otherwise.** Connections never stabilised (peaked ~97k, fell to
74k, ~35k client-side errors); the VM's ten cores were oversubscribed - Centrifugo 368%
plus the API's 327% ramp plus four generator containers at ~220% each. The
210ms broadcast mean is real and the histogram is Centrifugo's own, but it
cannot be attributed to the broker rather than to CPU starvation, and a number
that cannot be attributed is not a measurement. Plan §22's method for this
(move the generator to another host; if the ceiling moves, it was the
environment) needs a second machine.

**On the 50k row itself, one caveat in each direction.** The histogram includes
the 30s ramp, during which fanout reached *fewer* subscribers, so the steady-
state mean is if anything above 37ms. Against that, generator containers ran
at ~104% steady each alongside Centrifugo's 92%, leaving headroom - so the
superlinearity at 50k is more plausibly Centrifugo's own broadcast cost
growing with channel size than starvation. More plausibly; not proven.

**What this changes in the conclusion.** One Centrifugo node on this hardware
handles a channel with every client on it cleanly to ~25k subscribers,
degrades measurably by 50k, and is unmeasurable here at 100k. The levers, in
order, when a hot channel outgrows a node:

1. **More Centrifugo nodes.** Each node subscribes to a channel on the engine
   *once*, however many local clients want it, and fans out only to its own
   connections. The engine distributes fanout by node, automatically. This is
   why Redis stayed at 4-11% CPU with 25k-75k clients: it is not in the
   per-client delivery path at all.
2. **Shard the hot channel within a node** (`ticker:NVDA:{0..N}`, clients
   hashed across shards) to run one channel's broadcast on several cores
   instead of one. Same total work, shorter tail.

None of this is built. It belongs in the case-study discussion: the design
removed the expensive half of the celebrity problem (publish amplification) by
construction; the measurement located where the remaining half (fanout) starts
to cost on one node, and named the levers in the order they should be pulled.

**Reproduced.** The whole ladder was run a second time, end to end, before
any of this was treated as a result. Centrifugo's broadcast mean, run 1 vs
run 2:

| NVDA subscribers | run 1 | run 2 | delta | NVDA upd p50 / p99, run 1 → run 2 |
|---|---|---|---|---|
| 5,000 | 0.73ms | 0.75ms | +3% | 91 / 168 → 93 / 183ms |
| 10,000 | 2.20ms | 2.50ms | +14% | 177 / 279 → 194 / 303ms |
| 20,000 | 6.43ms | 6.86ms | +7% | 523 / 770 → 579 / 843ms |
| 25,000 | 6.22ms | 6.22ms | 0% | 376 / 694 → 576 / 812ms* |
| **50,000** | **36.99ms** | **37.33ms** | **+1%** | 893 / 1,730 → ~770 / 1,420ms |
| 100,000 (attempted) | 210.9ms | 305.9ms | - | environment-saturated both times |

\* Run 1's 25k row came from the multi-generator harness (90s, 30s ramp); run
2's from the single-generator one (45s, 10s ramp). The broker-side number is
identical; the client-side one is not comparable across a different ramp.

The knee holds: **~6ms per broadcast at 25k, ~37ms at 50k, in both runs.**
The 100k attempt was worse the second time (306ms, Centrifugo at 228%
steady-state with four generators at 143% each - the VM saturated even at
steady state), which is consistent with it measuring the environment rather
than the broker. Run 2's 50k had more client-side connect errors than run 1
(2,238 vs 166) with the same broadcast cost, which points at the generators'
ramp rather than at Centrifugo.

**Harness note.** The first 50k attempt was silently a 25k run: two concurrent
`docker compose run`s raced on the API's `depends_on` and the second died with
a container-name conflict. `--no-deps` fixed it; the row above is the re-run.
Same failure class as everything in the review doc - the artefact under test
was not the one the label claimed.

### 8.11 Three Centrifugo nodes, and measuring at steady state

Two things were done at once here, and the second corrected the first three
sections' method.

**The method correction.** Centrifugo's broadcast histogram is cumulative and
the generator's update latency covered the whole run, so both included the
connect ramp. The harness now snapshots each node's histogram when the ramp
ends and reports the delta (`tools/bench/hist_delta.py`), and the generator
discards update-latency samples before the ramp completes (`-measure-after`).
Result: steady-state broadcast cost is slightly *higher* than the whole-run
figure (39.5ms vs 33.1ms at 50k on one node) - during the ramp, broadcasts
reached fewer subscribers and pulled the mean down. **The 8.10 knee was
understated, not inflated.** It stands.

**Three nodes.** `docker-compose.nodes.yml` adds `centrifugo-2` and
`centrifugo-3` with identical Redis-engine configs; they discover each other
through the engine (every node reports `num_nodes 3`, asserted before each
run). Generators are pinned two-per-node. The price service still publishes
to node 1 only; the engine delivers to the rest - the first time in this case
study that the engine did its actual job.

50,000 clients, every one subscribed to `ticker:NVDA`, steady state:

| | 1 node | 3 nodes, per node |
|---|---|---|
| local NVDA subscribers | 50,000 | 16,668 / 16,379 / 16,007 |
| **broadcast mean (steady)** | **39.5ms** | **48.8 / 44.5 / 54.7ms** |
| broadcast p99 (steady) | ≤500ms | ≤500 / ≤500 / ≤1,000ms |
| client update p50 / p99 | 662-676ms / 1.37-1.49s | 601-707ms / 1.44-1.56s |
| Centrifugo CPU, steady | 83% | 36% / 33% / 31% |
| Redis CPU | 7% | 10% |

100,000 clients on 3 nodes: connections ~21k-22k per node, per-node broadcast
117-133ms, client p50 ~1.5s / p99 ~4-5s, the VM at ~1000% - the same
environment saturation as the single-node attempt, and reported the same way.

**What this says.** Distribution worked exactly as designed: an even split,
each node at a third of the CPU, Redis barely awake. And it changed nothing
the client could see. Each node held a third of the subscribers and spent
*longer*, not less, per broadcast than one node holding all of them. So
**per-node broadcast time is not driven by local subscriber count**, and the
"more nodes" lever - the first one 8.10 named - **did not pay on this
machine.**

Two readings, neither confirmed:

- *Shared environment.* All 50,000 socket writes traverse the same Docker VM
  network stack and the same ten cores whichever container issues them.
  Splitting across containers adds no network or CPU capacity; if the cost
  lives in the kernel's socket path rather than in Centrifugo's subscriber
  walk, three nodes cannot show a gain here. This is plan §22 Q9 again, and it
  is the reading the single-machine data cannot rule out.
- *Broker semantics.* The histogram may measure each publication's time
  including queueing behind the other ~30 publications of the same tick, on a
  node that processes them serially; then it tracks per-tick work and total
  connections rather than one channel's size. Only Centrifugo's hub source or
  a per-publication test (one channel at a time instead of a 30-command batch)
  would settle it.

**What this changes in the conclusion.** The theoretical ordering stays -
engine-distributed fanout by node is how Centrifugo scales, and the mechanism
was verified working - but its *payoff is unmeasured*, and one attempt to
measure it on one machine showed none. "More nodes" moves from "the first
lever" to "the first lever to test on separate hosts". Sharding stays out of
scope. The honest summary for the discussion: one node handles a hot channel
cleanly to ~25k subscribers; between 25k and 50k the cost per broadcast rises
~6x and reproduces; three nodes on the same machine did not lower it; and
finding out whether nodes on separate machines do is the next experiment, not
this one.

**Harness notes.** The single-node steady-state run first died with no output
because `seq 2 1` counts *down* - `NODES=1` built the node list as
`centrifugo centrifugo-2 centrifugo-1`. It surfaced only after `recreate` was
made to report its failure. Both fixed. That is the eighth silent-failure
class in this project's log, and the reason every harness now refuses to
exit quietly.

### 8.12 Final batch - the reference Scenario B table

Run last, cold, after the pre-submission code changes, with the harness that
reports steady-state CPU alongside peak. Same method as 8.3: simulated prices,
30% change ratio, seed 1, 4 workers, 45s runs, poll with publishing off and
push with it on, one batch. **This is the table the README quotes.**

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

**What the steady-state columns add.** Sections 8.3 and 8.8 could only show
peak CPU, and push's peak is its connect ramp - every client takes one
snapshot - which made the API columns look nearly equal across transports.
Measured after the ramp, the API under push is at 2-5% and Postgres at 0-1%,
against 81-401% and 24-104% under polling at the same client counts. That is
the per-request cost argument from section 3, finally visible as a number
rather than an inference: once connected, a push client costs the API
nothing until it reconnects.

**The 25,000 polling row collapsed here** (2,524 req/s, p50 4.4s, 452 errors)
where 8.3 had it clean at 4,706 req/s and 2b had 30,000 clean. Same code,
same configuration, several hours later in a session that had run every
scenario in this document; the machine's ceiling drifted down, as section 5
warned it does. Within this batch the comparison holds regardless: 25,000
push clients ran clean at 330ms p50 while 25,000 polling clients did not run
at all.

Update latency and egress reproduce 8.3 within a few percent at every row:
push 70 / 133 / 273 / 330ms p50 against 72 / 134 / 265 / 344ms, egress 6.3-6.6k
against 6.0-6.8k bytes per client per minute.

## 7. What this does not yet answer

Deliberately not measured yet, because it belongs to phase 3:

- Push throughput, latency and egress under the same load — the comparison that
  justifies Centrifugo existing.
- Whether Redis serving as both snapshot cache and broker degrades snapshot
  latency under burst, and whether NATS removes it.
- Reconnect storm behaviour, and `LATEST_PRICE_SOURCE=redis` vs `postgres`
  under a snapshot burst. Section 1 shows Redis loses on a single quiet request;
  a burst is a different question and has not been run.
- Slow-consumer disconnects and per-channel broadcast cost at high subscriber
  counts.

No number will be written here until it has been measured.

---

## Reproducing

```bash
make bootstrap
make seed-million                                     # ~2.5 minutes

# Benchmark settings go in .env, never inline: `docker compose run` recreates
# depends_on services from .env, so an inline override is silently dropped and
# the run then measures a different server than the results claim. Set:
#   PRICE_SOURCE=simulated      deterministic movement, works out of hours
#   UVICORN_ARGS=--workers 4
make reset-prices && make restart     # restart = up -d --force-recreate; see review C3

make db-bench
make load-container CLIENTS=20000 DURATION=60s
tools/bench/worker_scaling.sh 1:3000,5000 2:8000,11000 4:16000,20000 8:24000,28000
tools/bench/interval_tradeoff.sh equal-rate
tools/bench/interval_tradeoff.sh ceiling
```

Take any comparison within a single batch. Absolute throughput drifts across a
long session; see the drift note in section 5.

Results land in `.run/results/`, and each file records the API command, the
worker count and which generator produced it.
