#!/usr/bin/env bash
# slow consumers. A share of clients block per message; the
# server's outbound queue for each grows to client.queue_max_size and then the
# server disconnects them. There is no per-client conflation. Count it.
# The queue is set deliberately small for the run so it fills in seconds
# rather than hours at this message rate.
#   tools/bench/slow_consumers.sh 2000
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_simulated
CLIENTS="${1:-2000}"
QUEUE="${QUEUE:-8192}"
RANGE="$(user_range)"
${COMPOSE_PUSH} build load-generator >/dev/null
set_env TRANSPORT push; recreate price-service; wait_healthy price-service

echo "queue_max_size=${QUEUE} bytes for this run (default 1 MiB)"
CENTRIFUGO_CLIENT_QUEUE_MAX_SIZE="${QUEUE}" ${COMPOSE_PUSH} up -d --force-recreate --no-deps centrifugo >/dev/null 2>&1
wait_healthy centrifugo
# Centrifugo's own counter is the authority on who it disconnected and why.
# Code 3012 is "slow": the client's outbound queue exceeded queue_max_size.
# The generator's client-side count of the same thing proved unreliable
# (centrifuge-go does not surface the code the way it was checked), so it is
# reported only as "client-observed".
slow0="$(cf_metric_sum centrifugo_client_num_server_disconnects 'code="3012"')"
out="$(run_gen -transport push -clients "${CLIENTS}" -duration "${DURATION}" -ramp-up 8s -slow-fraction 0.10 -slow-delay 3s -logical-users "${LOGICAL_USERS}" ${RANGE})"
slow1="$(cf_metric_sum centrifugo_client_num_server_disconnects 'code="3012"')"
echo "${out}" | grep -E '^(publications recv|update latency|realtime errors)'
echo "slow readers:         $(python3 -c "print(int(${CLIENTS}*0.10))") clients (10%) block 3s per message"
echo "server disconnected:  $((slow1 - slow0)) of them with code 3012 (slow)   <- Centrifugo's counter"
echo "client-observed:      $(sed -n 's/.*server disconnected \([0-9]*\) of them.*/\1/p' <<<"${out}")   (generator-side; not trusted, see comment)"
echo "all server disconnects by code:"; curl -s http://localhost:8001/metrics | grep -E '^centrifugo_client_num_server_disconnects' | sed 's/^/  /' 
${COMPOSE_PUSH} up -d --force-recreate --no-deps centrifugo >/dev/null 2>&1; wait_healthy centrifugo
