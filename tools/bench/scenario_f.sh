#!/usr/bin/env bash
# Scenario F - celebrity ticker. Every connection also subscribes to one
# ticker, so its channel carries one subscriber per client. Per-channel
# broadcast cost comes from Centrifugo's own histogram; the client-side view
# is that channel's update latency against the rest.
#   tools/bench/scenario_f.sh 5000 10000 20000
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_simulated
TICKER="${TICKER:-NVDA}"
RANGE="$(user_range)"
${COMPOSE_PUSH} build load-generator >/dev/null
set_env TRANSPORT push; recreate price-service; wait_healthy price-service

printf '%-8s %10s %12s %12s %12s %10s %-60s\n' clients celeb_msgs all_upd_p99 celeb_p50 celeb_p99 cfugo_cpu broadcast_histogram_delta
printf '%.0s-' {1..130}; echo
for clients in "$@"; do
  recreate centrifugo; wait_healthy centrifugo   # fresh histogram per run
  stats="$(mktemp)"; sentinel="$(mktemp)"
  ( while [ -e "${sentinel}" ]; do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' $(${COMPOSE_PUSH} ps -q centrifugo) >> "${stats}" 2>/dev/null || true; sleep 2; done ) &
  out="$(run_gen -transport push -clients "${clients}" -duration "${DURATION}" -ramp-up 10s -celebrity "${TICKER}" -logical-users "${LOGICAL_USERS}" ${RANGE})"
  rm -f "${sentinel}"; wait 2>/dev/null || true
  celeb="$(grep "^celebrity" <<<"${out}")"
  msgs=$(sed -n 's/^celebrity [A-Z.]* *\([0-9]*\) messages.*/\1/p' <<<"${celeb}")
  cp50=$(sed -n 's/.*p50 \([0-9]*ms\).*/\1/p' <<<"${celeb}"); cp99=$(sed -n 's/.*p99 \([0-9]*ms\).*/\1/p' <<<"${celeb}")
  ap99=$(sed -n 's/^update latency *p50 [0-9]*ms *p95 [0-9]*ms *p99 \([0-9]*ms\).*/\1/p' <<<"${out}")
  cpu=$(awk '{gsub("%","",$2); if($2+0>m)m=$2+0} END{print m+0}' "${stats}")
  hist="$(cf_histogram centrifugo_node_broadcast_duration_seconds)"
  printf '%-8s %10s %12s %12s %12s %9s%% %-60s\n' "${clients}" "${msgs}" "${ap99}" "${cp50}" "${cp99}" "${cpu}" "${hist}"
  rm -f "${stats}"
done
