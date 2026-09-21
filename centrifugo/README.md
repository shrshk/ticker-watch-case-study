# Centrifugo

Two configs, one difference. Secrets come from the environment, not from here:
`CENTRIFUGO_CLIENT_TOKEN_HMAC_SECRET_KEY` (the same `JWT_SECRET` the API signs
with, so `POST /realtime/token` is a claims transform rather than a second auth
system) and `CENTRIFUGO_HTTP_API_KEY` (what the price service publishes with).

| file | engine / broker | what it means |
|---|---|---|
| `config.redis.json` | Redis engine, db 1 of the same Redis the price cache uses (db 0) | the default. Broker and cache share one event loop - the contention this case study measures. |
| `config.nats.json` | memory engine, NATS broker | the experiment. Only the cache is on Redis; fanout moves to NATS. At-most-once, no history - fine, because clients re-snapshot on reconnect. |

The `ticker` namespace: any authenticated client may subscribe (prices are not
user-private), no client may publish, no history (the snapshot path covers
reconnects, and it works identically under both brokers).

`client.queue_max_size` is the slow-consumer mechanism: a client whose
outbound queue exceeds 1 MiB is disconnected and recovers via the snapshot
path. There is no per-client conflation. Count the disconnects; do not pretend
they cannot happen.
