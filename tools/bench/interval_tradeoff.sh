#!/usr/bin/env bash
# Shortening the poll interval is the only way polling improves update latency.
# This measures what that costs.
#
# Two experiments:
#   equal-rate  - same requests/sec at three intervals. If the server behaves
#                 identically, request rate is the only thing it cares about
#                 and the interval merely sets the clients-per-rate exchange.
#   ceiling     - max clients at a 1s interval, to compare against the 5s one.
#
#   tools/bench/interval_tradeoff.sh equal-rate
#   tools/bench/interval_tradeoff.sh ceiling
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

DURATION="${DURATION:-40s}"

# The generator impersonates seeded users by id. Read the live range rather
# than assuming one: a reseed deletes and re-inserts, so ids move.
USER_MIN="$(cd "${ROOT}" && docker compose exec -T db psql -U postgres -tAc \
  "SELECT min(id) FROM users WHERE username LIKE 'load\_user\_%'" | tr -d '[:space:]\r')"
USER_MAX="$(cd "${ROOT}" && docker compose exec -T db psql -U postgres -tAc \
  "SELECT max(id) FROM users WHERE username LIKE 'load\_user\_%'" | tr -d '[:space:]\r')"
if [ -z "${USER_MIN}" ] || [ -z "${USER_MAX}" ]; then
  echo "no seeded load users; run 'make seed-small' first" >&2
  exit 1
fi
USER_RANGE_ARGS="-user-id-min ${USER_MIN} -user-id-max ${USER_MAX}"

LOGICAL_USERS="${LOGICAL_USERS:-1000000}"

docker compose --profile load build load-generator >/dev/null

run() {
  local interval="$1" clients="$2"
  local out
  local errfile
  errfile="$(mktemp)"
  out="$(docker compose --profile load run --rm load-generator \
          -clients "${clients}" -duration "${DURATION}" -interval "${interval}" \
          -logical-users "${LOGICAL_USERS}" ${USER_RANGE_ARGS} 2> "${errfile}")" || true
  if ! grep -q '^request rate' <<<"${out}"; then
    echo "generator produced no result for interval=${interval} clients=${clients}:" >&2
    grep -vE '^ (Container|Network)' "${errfile}" | head -20 >&2
    rm -f "${errfile}"
    exit 1
  fi
  rm -f "${errfile}"

  local rate p50 p95 p99 upd errs
  rate="$(sed -n 's/^request rate *//p' <<<"${out}")"
  p50="$(sed -n 's/^request latency *p50 \([^ ]*\).*/\1/p' <<<"${out}")"
  p95="$(sed -n 's/.*p95 \([^ ]*\)ms *p99.*/\1ms/p' <<<"$(grep 'request latency' <<<"${out}")")"
  p99="$(sed -n 's/.*p99 \([^ ]*\)ms *max.*/\1ms/p' <<<"$(grep 'request latency' <<<"${out}")")"
  upd="$(sed -n 's/^update latency *p50 \([^ ]*\).*/\1/p' <<<"${out}")"
  errs="$(sed -n 's/^transport errors *//p' <<<"${out}")"

  printf '%-10s %-9s %10s %10s %10s %10s %12s %8s\n' \
    "${interval}" "${clients}" "${rate:--}" "${p50:--}" "${p95:--}" "${p99:--}" "${upd:--}" "${errs:-0}"
}

header() {
  printf '%-10s %-9s %10s %10s %10s %10s %12s %8s\n' \
    interval clients req/s req_p50 req_p95 req_p99 update_p50 errors
  printf '%.0s-' {1..84}; echo
}

case "${1:-equal-rate}" in
  equal-rate)
    echo "Same ~3,000 req/s at three intervals (4 workers)."
    header
    run 5s 15000
    run 2s 6000
    run 1s 3000
    ;;
  ceiling)
    echo "Client ceiling at a 1s interval, to compare with the 5s ceiling."
    header
    run 1s 3000
    run 1s 4000
    run 1s 5000
    ;;
  *)
    echo "usage: $0 [equal-rate|ceiling]" >&2
    exit 1
    ;;
esac
