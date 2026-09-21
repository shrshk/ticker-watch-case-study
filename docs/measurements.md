# Measurements

Every number here was measured on the machine described below. Nothing is
estimated or copied from a vendor's benchmark. Where a number is a property of
the environment rather than of the design, it says so.

**How to read this.** Absolute throughput drifted downward over a long
session on this laptop (see *Methodology*), so every table is one back-to-back
batch and comparisons are made only within a table. Percentiles are p50 / p95 /
p99 unless stated. CPU percentages are of one core; the Docker VM has ten, so
1000% is full.

---

## Environment

| | |
|---|---|
| Host | Apple Silicon, 10 cores, 64 GB RAM, macOS |
| Docker Desktop VM | 10 vCPU, 31.3 GB RAM |
| Host `ulimit -n` | 1,048,576 |
| Host ephemeral ports | 16,384 (49152–65535) — caps a host-run load generator at ~16k keep-alive clients |
| Container ephemeral ports | 55,536 (set in Compose) |
| Postgres | 16, `max_connections=200` |
| Redis | 7, no persistence |
| Centrifugo | v6.9.6, Redis engine, one node unless stated |
| API | uvicorn, 4 workers for every load test |
| Prices | simulated random walk from real captured prices, 30% of tickers move per 5s tick, fixed seed |

**Docker Desktop interposes a VM and a network layer.** Load generated on the
host and sent through `localhost:8000` adds ~85ms at p99 with identical
throughput (2,872 vs 2,871 req/s) compared with the same load generated
inside the Compose network. Every load figure below is container-generated.
The load generators share the VM's ten cores with the services; at the
largest runs this is the binding constraint and is stated where it applies.

---

## 1. The database at one million users

Seeded with `COPY`: 1M users, 1M watchlists, 9.7M watchlist rows, ~1.24 GB,
in 136 seconds. Server-side latency, 500 iterations against random
watchlists, no HTTP:

| operation | 10k users | 100k users | 1M users |
|---|---|---|---|
| securities search | 0.16 / 0.23 / 0.25 ms | 0.16 / 0.23 / 0.26 | 0.16 / 0.23 / 0.27 |
| watchlist membership | 0.13 / 0.15 / 0.17 | 0.12 / 0.16 / 0.21 | 0.14 / 0.17 / 0.25 |
| snapshot via Redis | 0.17 / 0.28 / 0.36 | 0.17 / 0.27 / 0.38 | 0.17 / 0.29 / 0.47 |
| snapshot via Postgres | 0.14 / 0.16 / 0.20 | 0.13 / 0.16 / 0.20 | 0.14 / 0.18 / 0.25 |

**A hundredfold increase in rows moved p50 by hundredths of a millisecond.**
Every read is a point lookup on an indexed key — `watchlist_id = $1`,
`security_id = ANY($1)` — so row count does not enter. A million users is a
storage question, not a latency one.

**Redis was slower than Postgres for a single snapshot**, at every size.
`latest_prices` is ~100 rows entirely in `shared_buffers`; an in-memory B-tree
lookup in the same VM beats a network hop to Redis. What the cache is for is
measured in *2.5* and *3.6*.

---

## 2. Polling

### 2.1 The ceiling

Clients on a jittered 5s timer, each `GET /watchlist` returning membership and
prices. 4 uvicorn workers, one batch:

| clients | req/s | request p50 | p95 | p99 | errors | API CPU | DB CPU |
|---|---|---|---|---|---|---|---|
| 16,000 | 3,018 | 2.0ms | 7.3ms | 21.1ms | 0 | 234% | 79% |
| 20,000 | 3,772 | 3.8ms | 15.2ms | 27.7ms | 0 | 280% | 95% |
| 25,000 | 4,712 | 4.6ms | 19.3ms | 36.5ms | 0 | 343% | 119% |
| 27,500 | 5,182 | 7.8ms | 45.1ms | 85.0ms | 0 | 392% | 134% |
| **30,000** | **5,653** | **5.5ms** | **31.5ms** | **65.7ms** | **0** | **382%** | **155%** |
| 32,500 | 4,433 | 69.4ms | 11,256ms | 17,964ms | 87 | 408% | 157% |

**Polling breaks between 30,000 and 32,500 clients — about 5,650 requests a
second on this machine.** The failure is congestive collapse, not a plateau:
past the knee throughput *falls*, p50 rises hundreds-fold, and API memory
grows several-fold as requests queue. The API exhausts its 400% worker budget
first; Postgres is at ~150%, Redis at ~13%.

An earlier ladder put the knee at 25,000–27,500. That ladder was measured
while the read path performed an `UPDATE` on every request (a defect, see
*Methodology and corrections*); removing it moved the ceiling up ~20% and
halved DB CPU at every load.

### 2.2 Update latency is the interval, not the load

Measured at the client, from a price's `effective_at` to the moment a client
first observes it:

| clients | p50 | p95 | p99 |
|---|---|---|---|
| 1,000 | 2,548ms | 4,763ms | 4,970ms |
| 5,000 | 2,548ms | 4,765ms | 4,963ms |
| 15,000 | 2,508ms | 4,763ms | 4,956ms |
| 25,000 | 2,573ms | 4,763ms | 4,961ms |

Half an interval at p50, a full interval at p99, at every load. Holding the
load at 1,000 clients and varying only the interval:

| interval | p50 | p95 | p99 | p50 ÷ interval | p99 ÷ interval |
|---|---|---|---|---|---|
| 1s | 504ms | 947ms | 997ms | 0.50 | 1.00 |
| 2s | 1,005ms | 1,916ms | 1,989ms | 0.50 | 0.99 |
| 5s | 2,535ms | 4,769ms | 4,939ms | 0.51 | 0.99 |
| 10s | 4,604ms | 9,420ms | 9,877ms | 0.46 | 0.99 |

Latency moves twenty-fold while load does not move at all. It is the
signature of a uniform wait — a change lands at a random point in a fixed
cycle — and no CPU or memory term appears in it. **No server capacity improves
it.** The memory growth seen at collapse is the request backlog (228 MB →
1.1 GB against 31 GB available, while CPU sat at 383–422% of 400%): a symptom
of CPU saturation, not an independent limit.

### 2.3 Workers

One batch, 4 workers unless stated:

| workers | clients | req/s | p50 | p95 | p99 | API CPU | DB CPU |
|---|---|---|---|---|---|---|---|
| 1 | 5,000 | 936 | 2.2ms | 4.9ms | 14.5ms | 86% | 40% |
| 2 | 11,000 | 2,058 | 3.3ms | 14.8ms | 29.6ms | 162% | 98% |
| 4 | 20,000 | 3,743 | 10.4ms | 69.4ms | 121.4ms | 331% | 178% |
| 8 | 28,000 | 5,188 | 23.9ms | 270.4ms | 497.3ms | 543% | 289% |

| workers | req/s per worker | CPU per request |
|---|---|---|
| 1 | 936 | 1.35ms |
| 2 | 1,029 | 1.26ms |
| 4 | 936 | 1.36ms |
| 8 | 649 | 1.60ms |

Throughput per worker holds to four and drops to 69% of that at eight; p99
degrades much earlier than throughput. **The contended resource is the CPU run
queue, not Postgres.** Measured at 8 workers / 28,000 clients: 111–126 of 128
Postgres backends sat `idle, waiting on Client` — the database waiting for
the API to send work — while the VM's load average was 21.6–24.4 and
`procs_running` 27–44 against ten CPUs, with `procs_blocked` at 1–2 (nothing
on I/O). Past four workers, more workers are more contenders on a queue
already 2.4x deep; the added wait is scheduling delay, which is why p99 moves
before throughput does.

A connection-budget defect was found here and fixed: the asyncpg pool is *per
process*, so 8 workers × 16 = 128 connections exceeded the stock
`max_connections=100` and produced 31,641 errors that looked like a CPU
ceiling. Pool size is now configuration, `max_connections` is 200, and the
API logs the pool it opens.

### 2.4 Shortening the interval

The only polling lever for update latency. The server responds to request
rate, not interval — the same ~2,810 req/s at three intervals by scaling
clients to match:

| interval | clients | req/s | request p50 | p99 | update p50 |
|---|---|---|---|---|---|
| 5s | 15,000 | 2,805 | 3.1ms | 20.8ms | 2,518ms |
| 2s | 6,000 | 2,809 | 2.9ms | 19.1ms | 1,026ms |
| 1s | 3,000 | 2,811 | 3.1ms | 22.9ms | 495ms |

So the client ceiling divides by the same factor the interval does. At 1s and
4 workers: 3,000 / 4,000 / 5,000 clients ran at 2,811 / 3,745 / 4,679 req/s
with p99 11 / 21 / 141ms — roughly a quarter of the 5s ceiling for a fifth of
the latency. Fewer, busier connections were slightly cheaper to serve than
many idle ones.

| target update p50 | interval | clients per stack | stacks for 1M clients |
|---|---|---|---|
| 2.5s | 5s | ~30,000 | ~35 |
| 1.0s | 2s | ~12,000 | ~85 |
| 0.5s | 1s | ~5,000 | ~200 |

Measured egress: 16,500 bytes per client per minute, constant across all
loads — paid whether or not anything changed. At a 30% change ratio, roughly
70% of every response is data the client already had.

### 2.5 What the cache buys under polling

25,000 polling clients, the Redis cache bypassed and then in front:

| read path | req/s | request p50 | **p99** | API CPU | **DB CPU** | API + DB |
|---|---|---|---|---|---|---|
| postgres (bypassed) | 4,713 | 5.0ms | **118.8ms** | 316% | **147%** | 463% |
| redis (cache) | 4,712 | 3.4ms | **33.7ms** | 349% | **129%** | 478% |

Under sustained polling the cache is a **tail-latency and database-headroom
device**: p99 3.5x better and 17 points off Postgres, at the cost of ~30 API
points to do the `MGET` and decode. Total CPU is slightly higher with it. At
98 rows it cannot be a throughput device, because what it fronts is already an
in-memory point lookup.

---

## 3. Push

Centrifugo v6.9.6 behind a Compose profile. The price service publishes one
message per *changed* ticker per tick through Centrifugo's HTTP API; clients
subscribe to `ticker:<T>` channels, take one `GET /watchlist` snapshot after
subscribing, and drain. Update latency is measured at the client from the
publication's `effective_at` — the same metric, measured the same way, as
polling. The load generator opens real WebSocket connections.

### 3.1 Transport comparison

Same client counts under both transports, one batch, polling with the price
service not publishing and push with it publishing. CPU is the peak sample and
the **mean over the second half** of the run, once every client is connected.

| clients | transport | HTTP req/s | update p50 | p95 | p99 | errors | API CPU peak / **steady** | DB CPU peak / **steady** | Redis | Centrifugo peak / steady | bytes/client/min |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 5,000 | poll | 943 | 2,448ms | 4,769ms | 4,955ms | 0 | 109% / **84%** | 28% / **19%** | 3% | — | 16,559 |
| 5,000 | **push** | 111 | **70ms** | **135ms** | **164ms** | 0 | 106% / **2%** | 27% / **1%** | 4% | 39% / 10% | **6,280** |
| 10,000 | poll | 1,883 | 2,552ms | 4,768ms | 4,946ms | 1 | 277% / **192%** | 95% / **58%** | 9% | — | 16,536 |
| 10,000 | **push** | 222 | **133ms** | **286ms** | **331ms** | 0 | 242% / **2%** | 26% / **1%** | 7% | 59% / 16% | **6,268** |
| 20,000 | poll | 3,763 | 2,586ms | 4,770ms | 4,966ms | 0 | 398% / **375%** | 114% / **104%** | 11% | — | 16,528 |
| 20,000 | **push** | 440 | **273ms** | **671ms** | **734ms** | 0 | 358% / **2%** | 68% / **0%** | 10% | 131% / 20% | **6,598** |
| 25,000 | poll | 2,524 | 4,395ms | 13,418ms | 19,265ms | 452 | 412% / **401%** | 105% / **88%** | 11% | — | 8,898 |
| 25,000 | **push** | 550 | **330ms** | **602ms** | **894ms** | 0 | 332% / **5%** | 61% / **0%** | 8% | 118% / 26% | **6,397** |

**Update latency is the whole argument, and it holds at every size.** Polling
sits at ~2.5s p50 / ~4.95s p99 by arithmetic. Push is 70ms at 5,000 clients
and 330ms at 25,000 — under a second at p99 throughout.

**Push's latency grows with subscribers; polling's does not.** 70 → 133 → 273
→ 330ms p50 is roughly linear in client count. That is fanout — about 13,400
deliveries a second at 25,000 clients — and it is the one place push pays per
user. At 164% Centrifugo CPU for 13,437 deliveries/s, a delivery costs **about
0.12ms of CPU**, against the 1.35ms a poll costs across API and Postgres.

**After the connect ramp the API is nearly idle under push** — 2–5% and 0–1%
Postgres, against 84–401% and 19–104% under polling at the same counts.
Push's CPU lives in Centrifugo at 10–26%. Peak-vs-peak hides this, because
push's peak is the ramp: every client takes one snapshot.

**Egress is 2.4x lower** at this change ratio. On live market data, where
~40% of tickers moved per tick, the gap was only ~1.1x — push saves bandwidth
in proportion to what does *not* change.

**Redis did not notice the broker.** Redis CPU is identical under both
transports at every size. Thirty changed tickers per 5s tick is about six
broker messages a second.

The 25,000 polling row collapsed in this batch (452 errors, p50 4.4s) where
an earlier batch had it clean at 4,706 req/s; the machine's ceiling drifted
across a long session. In the same batch, 25,000 push clients ran with zero
errors and a 330ms p50.

### 3.2 Reconnect storm — cached vs uncached snapshot

10,000 push clients; at +25s half of them disconnect and reconnect at once.
Every reconnect re-runs the snapshot, so this is a 5,000-request burst on the
snapshot path, under both values of `LATEST_PRICE_SOURCE`:

| read path | reconnected | snapshot p50 | p95 | p99 | errors | API CPU | DB CPU |
|---|---|---|---|---|---|---|---|
| redis | 5,000 / 5,000 | 1.5ms | 8.3ms | 22.4ms | 0 | 94% | 28% |
| postgres | 5,000 / 5,000 | 1.4ms | 5.2ms | 19.2ms | 0 | 91% | 33% |

**The cache does not make the snapshot faster, even under the burst that
should have justified it.** It takes five points off Postgres. 5,000
simultaneous subscribe-plus-snapshot sequences completed with zero errors and
a 22ms p99 on both paths; the subscribe-then-snapshot ordering rule cost
nothing measurable.

### 3.3 Slow consumers

2,000 push clients, 10% of them blocking 3s on every publication.
Centrifugo's `client.queue_max_size` was lowered from 1 MiB to 8 KiB for the
run so the outbound queue fills in seconds rather than hours at this message
rate.

| | |
|---|---|
| slow readers | ~200 |
| **disconnected by Centrifugo, code 3012 (slow)** | **155** |
| update latency, all clients | p50 **39ms**, p95 3,014ms, p99 13,045ms, max 26s |
| realtime errors | 0 |

**Slow consumers hurt only themselves.** The p50 is 39ms — the fast 90%
never noticed. The tail *is* the slow clients, measured while still connected
and blocking, until the server's queue for them overflowed and it disconnected
them. That is Centrifugo's actual mechanism: a bounded per-client queue and a
disconnect; there is no per-client conflation. A disconnected client recovers
through the same subscribe-snapshot-drain path as any reconnect. At the
default 1 MiB queue and ~4 messages a second per client, a reader would need to
fall roughly twenty minutes behind before being cut.

The disconnect count is taken from Centrifugo's own counter. The generator's
client-side count read zero — it checked the disconnect code in a way the
client library does not surface — and is reported as untrusted.

### 3.4 The hot ticker

Every push client additionally subscribes to `ticker:NVDA`, so that channel
carries one subscriber per connection. Per-channel broadcast cost is
Centrifugo's own `node_broadcast_duration_seconds` histogram, reset per run;
the client-side view is NVDA's update latency against all channels.

**One node, single generator:**

| NVDA subscribers | broadcast: mean / p95 / p99 | per subscriber | NVDA update p50 / p99 | all-channel p99 | Centrifugo CPU |
|---|---|---|---|---|---|
| 5,000 | 0.73ms / ≤5 / ≤10ms | 0.15µs | 91 / 168ms | 167ms | 49% |
| 10,000 | 2.20ms / ≤25 / ≤25ms | 0.22µs | 177 / 279ms | 268ms | 58% |
| 20,000 | 6.43ms / ≤50 / ≤100ms | 0.32µs | 523 / 770ms | 730ms | 137% |
| 25,000 | 6.22ms / ≤50 / ≤100ms | 0.25µs | 376 / 694ms | 644ms | 115% |

**Two and four generators in parallel, steady state (after the connect ramp):**

| NVDA subscribers | connections reached | broadcast mean (steady) | NVDA update p50 vs all-channel p50 | Centrifugo CPU steady | Centrifugo memory | client errors |
|---|---|---|---|---|---|---|
| 50,000 | 49,919 | **39.5ms** | **+39%** (893 vs 638ms) | 83% | 2.8–3.3 GiB | 166 (0.3%) |
| 100,000 (attempted) | 74k–97k, unstable | 210–306ms | — | 149–228% | 5.7–7.0 GiB | ~35,000 |

**Publish cost is flat by construction** — one publish per changed ticker,
however many watchers. **Fanout cost is linear in subscribers to ~25k** (about
0.3µs per subscriber per publication, 6ms to reach 25,000) and the hot
channel's tail sits within 5% of every other channel's. **Between 25k and 50k
the cost per broadcast rises ~6x** and the hot channel's p50 pulls 39% ahead
of the rest. The whole ladder was run twice; broadcast means reproduced within
1–14% at every point and within 1% at 50k (36.99 vs 37.33ms).

**100,000 was beyond this environment.** Connections never stabilised and the
VM's ten cores were oversubscribed by Centrifugo (368%), the API's connect
ramp (327%) and four generator containers (~220% each). A broadcast time that
cannot be attributed to the broker rather than to CPU starvation is not a
measurement, and is reported as such.

**Three Centrifugo nodes on the same machine.** Two more nodes with identical
Redis-engine configs discovering each other through the engine (each reported
`num_nodes 3`), generators pinned two per node, 50,000 clients, steady state:

| | 1 node | 3 nodes, per node |
|---|---|---|
| local NVDA subscribers | 50,000 | 16,668 / 16,379 / 16,007 |
| **broadcast mean (steady)** | **39.5ms** | **48.8 / 44.5 / 54.7ms** |
| client update p50 / p99 | 662–676ms / 1.4–1.5s | 601–707ms / 1.4–1.6s |
| Centrifugo CPU, steady | 83% | 36% / 33% / 31% |
| Redis CPU | 7% | 10% |

Distribution worked exactly as designed — an even split, each node at a third
of the CPU, Redis barely awake — and it changed nothing the client could see.
Each node held a third of the subscribers and spent as long or longer per
broadcast than one node holding all of them, so per-node broadcast time is not
driven by local subscriber count. **A local Docker experiment neither
warrants "more nodes help" nor refutes it:** three containers share one VM's
cores and one virtual network stack that every socket write goes through. On
cloud infrastructure — three hosts, three network interfaces, a real engine
hop between them — the same lever could be load-bearing, and that is where it
has to be measured.

### 3.5 Broker: Redis engine vs NATS

The same 10,000-client push load and the same 5,000-client reconnect storm
under Centrifugo's Redis engine and then a NATS broker. Nothing in the
application changed between the two rows: a different Centrifugo config file
and one more container.

| broker | update p50 | p95 | p99 | storm snapshot p50 / p95 / p99 | errors | Redis CPU | Centrifugo CPU |
|---|---|---|---|---|---|---|---|
| Redis engine | 127ms | 267ms | 307ms | 1.4 / 7.5 / 22.0ms | 0 | 3.7% | 58% |
| NATS broker | 133ms | 247ms | 267ms | 1.5 / 6.7 / 22.3ms | 0 | 3.6% | 59% |

**Indistinguishable.** Redis serving as both the price cache and the broker
was expected to contend under load; at about six broker messages a second it
does not, and Redis CPU is 3.7% with the broker on it and 3.6% without. The
swap proved the claim the architecture makes about itself — the price service
publishes through Centrifugo's API, so changing the broker touched two config
files and zero lines of code — and bought nothing. NATS gives up history and
recovery; the snapshot-on-reconnect path never used them. Redis stays.

### 3.6 What the Redis cache is for

Three measurements: slower than Postgres for one snapshot (*1*), no faster
under a 5,000-request burst (*3.2*), a tail-latency and DB-headroom device
under 25,000 polling clients (*2.5*). Under push it is touched once per
connection and once per reconnect, and is nearly idle.

The conclusion is narrower than "drop the cache". **On this machine the cache
is not load-bearing on push.** But this machine is one Docker VM: Postgres,
Redis, the API and the load share ten cores and a loopback network, and a
`latest_prices` lookup beats a Redis round trip here because both are
in-memory calls to a neighbour. On cloud infrastructure the same two paths are
a managed Postgres across a subnet and a managed Redis across another, under a
connection budget the API shares with everything else — and there the cache
could be load-bearing for exactly the reasons it was designed in. The local
experiment does not warrant removing it; it warrants saying it was not needed
here and measuring again where it might be. The toggle
(`LATEST_PRICE_SOURCE`) stays in the code for that reason, and the cache stays
with it.

---

## 4. Watcher distribution

Popularity is Zipf-distributed (s = 1.1) across 1M users and 9.68M rows:

| rank | ticker | watchers | share | cumulative |
|---|---|---|---|---|
| 1 | NVDA | 958,428 | 9.91% | 9.9% |
| 2 | TSLA | 787,540 | 8.14% | 18.0% |
| 3 | AAPL | 629,354 | 6.50% | 24.6% |
| 4 | AMZN | 513,858 | 5.31% | 29.9% |
| 5 | META | 429,113 | 4.44% | 34.3% |
| 10 | COIN | 228,055 | 2.36% | 49.3% |
| 99 | CMG | 20,393 | 0.21% | 100% |

Ten tickers carry half of all subscriptions; the tail sits near 20,000. At
25,000 connections with ten subscriptions each, NVDA's share means roughly
every connected client subscribes to it — which is why the hot-ticker
measurements in *3.4* use it.

The ranking is an explicit popularity list in the seeder. An earlier version
ranked by security id, which is alphabetical, and produced a distribution with
the correct shape and absurd content — Airbnb the second most-watched stock,
NVDA in the tail. Load follows the shape, so no load number changed; what it
would have broken is any conclusion about *which* ticker is hot.

---

## 5. Methodology and corrections

**Only compare within a batch.** The same configuration — 4 workers, 25,000
polling clients — produced 4,782 req/s with an 88.8ms p99 early in a session
and 3,810–4,011 req/s with a 9–12s p99 several hours later, across three
consecutive runs that agreed with each other. Not the application and not
database bloat (`latest_prices` is rewritten every tick but autovacuum held it
at 72 kB). Most likely thermal, on a laptop at sustained multi-core load for
hours; macOS recorded no warning, so that is inference. Every table above is
one batch.

**Measure at steady state.** Centrifugo's broadcast histogram is cumulative
and the generator's latency samples covered whole runs, so both included the
connect ramp — during which fanout reaches *fewer* subscribers and pulls the
mean down. The hot-ticker harness now snapshots each node's histogram when the
ramp ends and reports the delta, and the generator discards samples taken
before it. Steady-state figures came out slightly *higher* than whole-run ones
(39.5 vs 33.1ms at 50k), so earlier whole-run figures understated the cost.

**Separate the application from the environment.** Two discriminators were
used: the same load generated on the host and inside the Compose network
(throughput identical, tail 7x worse on the host — the port forwarder's cost,
not the application's); and total VM CPU accounting on every large run, so a
saturated machine is reported as a saturated machine.

**Corrections applied to earlier measurements, stated so the tables can be
trusted:**

- *The read path wrote on every poll.* Resolving a user's watchlist was an
  `INSERT … ON CONFLICT DO UPDATE`, which Postgres executes as a real update
  even when nothing changes; `pg_stat_user_tables` showed 8.8M updates on
  `watchlists` from polling alone. Every load number carried it. Now a
  `SELECT`, with the insert only on a miss; the polling ceiling moved from
  ~25k to ~30k and DB CPU roughly halved. *2.1* is the corrected ladder.
- *Every request looked the user up.* On top of verifying a stateless token.
  The caller's identity now comes from the token's claims; the revocation gap
  this opened is closed by 15-minute access tokens with rotated refresh tokens.
- *Benchmarks on live prices.* The first transport comparison ran during
  market hours and crossed 16:00 ET; its last three rows measured a frozen
  market as "no updates". Every harness now refuses to run unless prices are
  simulated, in `.env` and in the running container.
- *Stale artefacts under test.* A recreated single-worker API, a load-generator
  image built from an older Go module, a bind-mounted `node_modules` that
  outlived its image, a new Python dependency absent from the image, two
  concurrent generator containers racing on a shared dependency, and a
  harness that swallowed its own failure — each produced numbers that looked
  real and were not. Every harness now rebuilds what it tests, asserts the
  service under test is configured as the results will claim, and exits
  non-zero on a silent run. The rule that came out of it: **a number is not
  evidence until the thing it summarises has been looked at directly.**

---

## 6. Questions this set out to answer

| question | answer |
|---|---|
| At what client count does 5-second polling stop being the right answer, and what does push cost? | Polling breaks at ~30,000 clients on this machine (*2.1*) with update latency fixed at ~2.5s p50 by arithmetic (*2.2*). Push holds 70–330ms p50 across 5k–25k at 2.4x less egress and near-zero API and DB CPU at steady state (*3.1*); it costs connection state, one snapshot per connect, and fanout CPU that grows with subscribers at ~0.12ms per delivery. |
| Can Postgres support 1M users and 10M watchlist rows? | Yes; read latency was flat from 10k to 1M users (*1*). |
| What is snapshot latency at scale, cached and uncached? | Sub-millisecond either way for one request; under a 5,000-request burst, 22ms p99 either way (*3.2*); the cache earns its keep only under sustained polling (*2.5*). |
| How many realtime connections can one Centrifugo node sustain locally? | 25,000 connections with 241,737 subscriptions and zero errors at 164% CPU; 50,000 with 0.3% errors at 83% steady-state CPU. The generator, not Centrifugo, is the next local limit. |
| Does one Redis doing cache and broker degrade snapshot latency, and does NATS remove it? | No degradation exists to remove at ~6 broker messages a second (*3.5*). |
| How does per-channel broadcast cost scale toward 500k subscribers? | Linearly to ~25k (~0.3µs per subscriber), then ~6x between 25k and 50k, reproduced; the hot channel's tail separates between 25k and 50k. 100k exceeded this environment; three nodes on one machine did not lower it (*3.4*). |
| What happens to slow clients? | Centrifugo disconnects them; nobody else notices (*3.3*). |
| Does `latest_prices` persistence affect realtime latency? | No: the durable write runs concurrently with the cache write and the publish and is not on the delivery path. Not stress-tested with an artificially slowed Postgres. |
| What is the first local bottleneck, and is it infrastructure or application? | Under polling, the API's worker CPU (*2.1*), with the CPU run queue behind it (*2.3*). Under push, the load generator and the shared VM (*3.4*). The host port forwarder and the ephemeral port range are environment, and were separated as such. |
| How would this scale horizontally? | Centrifugo's engine distributes fanout by node (verified: even split across three); whether that lowers latency needs separate hosts (*3.4*). The API is stateless behind any load balancer; Postgres reads are point lookups. |

---

## 7. Not measured

- **Postgres slowdown under realtime load.** The durable write is off the
  delivery path by construction; it was not shown under an artificially
  stalled Postgres.
- **Centrifugo's own ceiling.** 50,000 connections on one node ran with 0.3%
  errors; the single load-generator container is the likelier next limit.
- **Anything on separate hosts** — the cache across a real network, Centrifugo
  nodes on separate machines, 100,000 connections. Each of these is where the
  design's remaining claims would be tested.

---

## Reproducing

```bash
# In .env: PRICE_SOURCE=simulated and UVICORN_ARGS=--workers 4, then:
make restart
make seed-million                                   # ~2.5 minutes
make db-bench                                       # section 1
make load-container CLIENTS=25000 DURATION=60s      # one polling run
tools/bench/worker_scaling.sh 1:5000 2:11000 4:20000 8:28000
tools/bench/interval_tradeoff.sh equal-rate
tools/bench/transport_comparison.sh 5000 10000 20000 25000
tools/bench/reconnect_storm.sh 10000
tools/bench/slow_consumers.sh 2000
tools/bench/hot_ticker.sh 5000 10000 20000 25000
tools/bench/hot_ticker_scale.sh 50000 2
NODES=3 tools/bench/hot_ticker_scale.sh 50000 6
tools/bench/broker_comparison.sh 10000
```

Every harness refuses to run on live prices, rebuilds the load-generator
image, asserts the service under test is configured as the results will
claim, and exits non-zero on a silent run. Results land in `.run/results/`
with the API command and worker count recorded.
