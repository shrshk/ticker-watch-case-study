#!/usr/bin/env bash
# Scenario E - slow consumers. A share of clients block per message; the
# server's outbound queue for each grows to client.queue_max_size and then the
# server disconnects them. There is no per-client conflation. Count it.
# The queue is set deliberately small for the run so it fills in seconds
# rather than hours at this message rate.
#   tools/bench/scenario_e.sh 2000
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
CLIENTS="${1:-2000}"
QUEUE="${QUEUE:-8192}"
RANGE="$(user_range)"
${COMPOSE_PUSH} build load-generator >/dev/null
set_env TRANSPORT push; recreate price-service; wait_healthy price-service

echo "queue_max_size=${QUEUE} bytes for this run (default 1 MiB)"
CENTRIFUGO_CLIENT_QUEUE_MAX_SIZE="${QUEUE}" ${COMPOSE_PUSH} up -d --force-recreate --no-deps centrifugo >/dev/null 2>&1
wait_healthy centrifugo
d0="$(cf_metric_sum centrifugo_client_num_reply_errors)"; disc0="$(cf_metric_sum centrifugo_transport_connections_total 2>/dev/null || echo 0)"
out="$(run_gen -transport push -clients "${CLIENTS}" -duration "${DURATION}" -ramp-up 8s -slow-fraction 0.10 -slow-delay 3s -logical-users "${LOGICAL_USERS}" ${RANGE})"
echo "${out}" | grep -E '^(slow consumers|disconnects|publications recv|update latency|realtime errors)'
echo "centrifugo disconnect reasons:"; curl -s http://localhost:8001/metrics | grep -E '^centrifugo_(node_num_clients |transport_connections_closed|client_.*disconnect)' | head -8
${COMPOSE_PUSH} up -d --force-recreate --no-deps centrifugo >/dev/null 2>&1; wait_healthy centrifugo
