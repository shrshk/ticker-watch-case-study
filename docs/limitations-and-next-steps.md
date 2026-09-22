# Known limitations and next steps

What this build does not do, what it does only within stated bounds, and what
the next measurements would be. Ordered by how much they would matter in a
deployed system.

---

## Where the measurements stop

Everything in `measurements.md` was taken on one laptop running one Docker VM.
That environment verifies the *design* — the write guards, the fanout
mechanism, the subscribe-then-snapshot ordering, the transport comparison —
and cannot measure the *infrastructure* those components exist for. Three
findings in particular are true of this machine and unproven beyond it:

- **The Redis price cache is not load-bearing on push.** Here, Postgres and
  Redis are in-memory neighbours on a loopback. On a managed Postgres across a
  subnet the cache could be load-bearing for exactly the reasons it was
  designed in. It stays, with its toggle, so the question can be asked again
  where it matters.
- **Three Centrifugo nodes did not reduce fanout latency.** They shared one
  VM's cores and one virtual network stack. The distribution mechanism was
  verified working; its payoff needs separate hosts.
- **100,000 connections could not be measured.** The load generators saturated
  the VM before Centrifugo did. The next local limit is the generator, not the
  broker.

The next measurement is the same harnesses on three hosts with a real network
between them. Nothing in the code needs to change for that; the Compose
overrides for extra nodes and the alternate broker are already the shape of
the deployment.

---

## Not built, by decision

| | why | what it would take |
|---|---|---|
| **Price history** | The brief asks for current prices and persisted *user* data; a time series answers no question it asks. | An append-only partitioned table beside `latest_prices`, monthly partitions, a retention job. Enables charts, portfolio history, backtests. |
| **Identity provider** (Keycloak or any OIDC) | A large container with a realm import to bootstrap, for a case study with two demo users. | Replace `users`, `/auth/login` and the HS256 secret with JWKS validation; Centrifugo already supports JWKS. |
| **Hot-channel sharding** | Measured as unnecessary below ~25k subscribers per node; the knee between 25k and 50k was found on one machine and is not attributable to the broker there. | `ticker:NVDA:{0..N}` with clients hashing into a shard; N publishes instead of one. Only after the more-nodes measurement on real hosts. |
| **Market-session behaviour** | Interesting product logic; dilutes the scaling question. | A calendar, per-asset-class hours, and a "stale" state in the UI. |
| **Postgres slowdown under realtime load** | The durable write runs concurrently with the cache write and the publish, so it is off the delivery path by construction; this was reasoned, not stressed. | A `pg_sleep` trigger on `latest_prices` under push load; expect unchanged delivery latency and a growing upsert-error counter. |

---

## Known rough edges

Small, understood, and left as they are with the reason.

| | effect | reason left |
|---|---|---|
| Simulated prices resume from wherever the walk had reached | A restart mid-session is not reproducible from `SIM_SEED`; only a start from an empty `latest_prices` is. | Benchmarks reset prices first; demos do not need reproducibility. |
| `round(price, 2)` in the simulator | Sub-$5 tickers barely move at 0.2% volatility (ACB at $3.87: most moves round to zero), so the effective change ratio is below the configured 30% for the tail. | Cosmetic for a demo; the hot ticker in every measurement was NVDA. |
| A Postgres connection is held across the Redis `MGET` in the snapshot path | Under a Redis stall the API's DB pool drains while waiting on Redis. | Acquire-per-query is a larger refactor of the handler layer; the fallback path is correct, just slower to fail over. |
| Redis has no `maxmemory` policy | Irrelevant while it holds 98 price keys and an unused history; relevant if Centrifugo history were enabled. | History is off by design — the snapshot path covers reconnects. |
| Development bind mounts and `--reload` in the shipped Compose | The API image is not quite self-contained at runtime. | A reviewer editing a file and seeing it live is worth more than purity; the image does contain the code, and removing the mounts is three lines. |
| Dev-only secrets in `.env` | `JWT_SECRET` and `CENTRIFUGO_HTTP_API_KEY` ship as obvious placeholders. | A case study on one laptop; `make bootstrap` could generate them. |
| `_` and `%` in a search query act as LIKE wildcards | `_` matches everything. | Harmless at 99 rows; escape them if the catalog grows. |
| `make up` on a fresh volume without `db/demo.sql` | The price service waits up to 60s for `make migrate`, then exits and is restarted. | `make bootstrap` is the documented path and runs migrate; the shipped `demo.sql` makes the wait moot. |
| Containers run as root | Standard for the base images used. | Case study scope. |
| The vendor is a stand-in with in-process state | Restarting the `vendor` service restarts its walk from `seed/prices.csv`; stored `api` prices jump back once. | The original vendor was the case study's own API. The stand-in keeps the adapter, the one-call-per-tick discipline and the edge cases exercised with no external dependency. |
| The load generator's client-side disconnect-code count is untrusted | It read zero while Centrifugo counted 155 slow disconnects. | The harness reports the broker's counter; fixing the client-side check buys nothing until something needs it. |

---

## What we would do differently

Most of the defects found during the build had the same shape: a summary
statistic agreed with expectation, and the thing underneath it was never
inspected. A Zipf distribution with the right exponent and the wrong tickers;
API CPU at 500% while Postgres was rejecting connections; three benchmark runs
agreeing with each other while measuring a silently recreated single-worker
server; "two Postgres round trips" written without counting; a stale image, a
stale volume, a missing `go.sum`, a harness that swallowed its own failure.

The rule that came out of it is now built into the tooling — every harness
rebuilds what it tests, asserts the configuration under test, and refuses to
exit quietly — and it is the one thing this build would pass on: **a number is
not evidence until the thing it summarises has been looked at directly.**
