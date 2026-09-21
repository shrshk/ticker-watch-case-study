#!/usr/bin/env bash
# reconnect storm. Half the clients drop and reconnect at once;
# every reconnect is a snapshot, so this is a GET /watchlist burst.
# Run it under both read paths (LATEST_PRICE_SOURCE=redis|postgres) so the
# caching decision is measured under the one load that should justify it.
#   tools/bench/reconnect_storm.sh 10000
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_simulated
CLIENTS="${1:-10000}"
RANGE="$(user_range)"
${COMPOSE_PUSH} build load-generator >/dev/null
set_env TRANSPORT push; recreate price-service; wait_healthy price-service

printf '%-9s %8s %10s %10s %10s %8s %9s %8s\n' read_path storm snap_p50 snap_p95 snap_p99 errors api_cpu db_cpu
printf '%.0s-' {1..80}; echo
for rp in redis postgres; do
  set_env LATEST_PRICE_SOURCE "${rp}"; recreate api; wait_healthy api; assert_env api LATEST_PRICE_SOURCE "${rp}"
  stats="$(mktemp)"; sentinel="$(mktemp)"
  ( while [ -e "${sentinel}" ]; do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' $(${COMPOSE_PUSH} ps -q api db) >> "${stats}" 2>/dev/null || true; sleep 2; done ) &
  out="$(run_gen -transport push -clients "${CLIENTS}" -duration "${DURATION}" -ramp-up 10s -storm-at 25s -storm-fraction 0.5 -logical-users "${LOGICAL_USERS}" ${RANGE})"
  rm -f "${sentinel}"; wait 2>/dev/null || true
  storm="$(field storm "${out}")"
  errs=$(( $(field 'realtime errors' "${out}") + $(field 'non-200 snapshots' "${out}") ))
  api=$(awk '/-api-/{gsub("%","",$2); if($2+0>m)m=$2+0} END{print m+0}' "${stats}"); db=$(awk '/-db-/{gsub("%","",$2); if($2+0>m)m=$2+0} END{print m+0}' "${stats}")
  p50=$(sed -n 's/.*storm p50 \([0-9.]*ms\).*/\1/p' <<<"${storm}"); p95=$(sed -n 's/.*p95 \([0-9.]*ms\).*/\1/p' <<<"${storm}"); p99=$(sed -n 's/.*p99 \([0-9.]*ms\).*/\1/p' <<<"${storm}")
  n=$(sed -n 's/^\([0-9]*\) of .*/\1/p' <<<"${storm}")
  printf '%-9s %8s %10s %10s %10s %8s %8s%% %7s%%\n' "${rp}" "${n}" "${p50}" "${p95}" "${p99}" "${errs}" "${api}" "${db}"
  rm -f "${stats}"
done
set_env LATEST_PRICE_SOURCE redis; recreate api; wait_healthy api
