#!/usr/bin/env bash
# broker comparison. The same push load and the same reconnect
# storm under Centrifugo's Redis engine and under a NATS broker. No
# application code changes between the two - only which config Centrifugo
# loads and which container is running.
#   tools/bench/broker_comparison.sh 10000
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_simulated
CLIENTS="${1:-10000}"
RANGE="$(user_range)"
${COMPOSE_PUSH} build load-generator >/dev/null
set_env TRANSPORT push; set_env LATEST_PRICE_SOURCE redis; recreate price-service api; wait_healthy api

printf '%-6s %8s %9s %9s %9s %10s %10s %8s %9s %8s\n' broker upd_p50 upd_p95 upd_p99 storm_p50 storm_p95 storm_p99 errors redis_cpu cfugo_cpu
printf '%.0s-' {1..100}; echo
for broker in redis nats; do
  set_env BROKER "${broker}"
  if [ "${broker}" = nats ]; then
    docker compose -f docker-compose.yml -f docker-compose.nats.yml --profile push --profile load up -d --force-recreate --no-deps nats centrifugo >/dev/null 2>&1
  else
    docker compose -f docker-compose.yml --profile push --profile load up -d --force-recreate --no-deps centrifugo >/dev/null 2>&1
    docker compose -f docker-compose.yml -f docker-compose.nats.yml --profile push stop nats >/dev/null 2>&1 || true
  fi
  wait_healthy centrifugo
  eng="$(docker inspect -f '{{range .Mounts}}{{.Source}}{{end}}' "$(${COMPOSE_PUSH} ps -q centrifugo)" | sed 's/.*config\.\(.*\)\.json.*/\1/')"
  [ "${eng}" = "${broker}" ] || { echo "REFUSING: centrifugo mounts config.${eng}.json, wanted ${broker}" >&2; exit 1; }
  stats="$(mktemp)"; sentinel="$(mktemp)"
  ( while [ -e "${sentinel}" ]; do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' $(${COMPOSE_PUSH} ps -q redis centrifugo) >> "${stats}" 2>/dev/null || true; sleep 2; done ) &
  out="$(run_gen -transport push -clients "${CLIENTS}" -duration "${DURATION}" -ramp-up 10s -storm-at 25s -storm-fraction 0.5 -logical-users "${LOGICAL_USERS}" ${RANGE})"
  rm -f "${sentinel}"; wait 2>/dev/null || true
  upd=$(sed -n 's/^update latency *p50 \([0-9]*ms\) *p95 \([0-9]*ms\) *p99 \([0-9]*ms\).*/\1 \2 \3/p' <<<"${out}")
  storm="$(field storm "${out}")"; sp=$(sed -n 's/.*storm p50 \([0-9.]*ms\) p95 \([0-9.]*ms\) p99 \([0-9.]*ms\).*/\1 \2 \3/p' <<<"${storm}")
  errs=$(( $(field 'realtime errors' "${out}") + $(field 'non-200 snapshots' "${out}") ))
  rc=$(awk '/-redis-/{gsub("%","",$2); if($2+0>m)m=$2+0} END{print m+0}' "${stats}"); cc=$(awk '/-centrifugo-/{gsub("%","",$2); if($2+0>m)m=$2+0} END{print m+0}' "${stats}")
  printf '%-6s %8s %9s %9s %9s %10s %10s %8s %8s%% %7s%%\n' "${broker}" ${upd} ${sp} "${errs}" "${rc}" "${cc}"
  rm -f "${stats}"
done
set_env BROKER redis
