# Measurements

Every number here was measured on the machine described below. Nothing is
estimated, extrapolated or copied from a vendor's benchmark. Where a number is
an artefact of the environment rather than a property of the design, it says so.

Raw run logs are written to `.run/results/` and are not committed.

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
capacity improves it. The only levers are shortening the interval — which
multiplies request rate linearly — or pushing.

This is the number phase 3 has to beat, and the reason the comparison is worth
running.

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

## 5. Does the ceiling move with CPU?

The plan's discriminator for "application limit or infrastructure limit": change
the CPU available to the application and see whether the ceiling moves with it.

| workers | clients | req/s | p50 | API CPU | DB CPU | verdict |
|---|---|---|---|---|---|---|
| 4 | 25,000 | 4,782 | 7.8ms | 383% | 194% | healthy, API at 96% of budget |
| 4 | 30,000 | 3,758 | 4,045ms | 422% | 273% | collapsed |
| 8 | 30,000 | 3,193 | 74ms | 509% | 284% | collapsed, and no better |

Doubling the workers did **not** move the ceiling. Full accounting during the
8-worker run explains why:

```
api              509.5%
db               284.3%
load-generator    48.9%
redis              9.2%
client/price-svc   0.1%
------------------------
TOTAL            851.8%   of 1000% available
```

At four workers the limit is the application's own CPU budget: the API saturates
its 400% while the machine still has headroom. At eight workers that budget is
gone and the limit becomes the machine — API plus Postgres alone consume 79% of
every core in the VM, and past roughly 85% utilisation scheduling latency
dominates and throughput degrades.

So the honest statement is: **the 4-worker ceiling is an application
configuration limit, and the hardware ceiling is immediately behind it.** On
this laptop the two are about the same number, which is why "add more workers"
was not a fix.

One caveat stated plainly: the load generator shares the VM with the server, so
it consumes CPU the server could have used. It is small — 49% of 1000%, about
half a core — but it is not zero, and a dedicated load machine would move these
numbers up somewhat.

---

## 6. What this does not yet answer

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
make reset-prices && make up PRICE_SOURCE=simulated   # deterministic movement
make seed-million                                     # ~2.5 minutes
make db-bench

# Set UVICORN_ARGS=--workers 4 in .env first - see the note in .env.example.
make load-container CLIENTS=25000 DURATION=60s
```

Results land in `.run/results/`, and each file records the API command, the
worker count and which generator produced it.
