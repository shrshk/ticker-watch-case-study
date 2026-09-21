#!/usr/bin/env bash
# Run the load generator while sampling container CPU and memory, so that
# "what saturated first" is evidence rather than a guess.
#
#   tools/bench/run_load.sh CLIENTS DURATION [EXTRA_LOADGEN_ARGS...]
set -euo pipefail

CLIENTS="${1:?usage: run_load.sh CLIENTS DURATION [args...]}"
DURATION="${2:?usage: run_load.sh CLIENTS DURATION [args...]}"
shift 2

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BIN="${ROOT}/.run/loadgen"
OUT="${ROOT}/.run/results"
mkdir -p "${OUT}" "$(dirname "${BIN}")"

# GENERATOR=host runs the generator natively; GENERATOR=container runs it
# inside the Compose network. Comparing the two separates an application
# ceiling from an environment ceiling - see README question 9.
GENERATOR="${GENERATOR:-host}"

if [ "${GENERATOR}" = "host" ]; then
  echo "building the load generator"
  (cd "${ROOT}/tools/load_generator" && go build -o "${BIN}" .)
else
  (cd "${ROOT}" && docker compose --profile load build load-generator >/dev/null)
fi


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

STAMP="$(date +%Y%m%d-%H%M%S)"
STATS="${OUT}/stats-${CLIENTS}c-${STAMP}.log"
RESULT="${OUT}/load-${CLIENTS}c-${STAMP}.log"
SENTINEL="${OUT}/.sampling-${STAMP}"
touch "${SENTINEL}"

# Sample only this project's containers - another Compose stack on the same
# machine would otherwise land in the results. The sampler stops when the
# sentinel file disappears, which avoids signalling a background process.
CONTAINERS="$(cd "${ROOT}" && docker compose --profile push ps -q | tr '\n' ' ')"
if [ -z "${CONTAINERS// /}" ]; then
  echo "no containers running; start the stack with 'make up-detached'" >&2
  exit 1
fi

(
  while [ -e "${SENTINEL}" ]; do
    # shellcheck disable=SC2086
    docker stats --no-stream ${CONTAINERS} \
      --format '{{.Name}}	{{.CPUPerc}}	{{.MemUsage}}' 2>/dev/null \
      | sed "s/^/$(date +%s)	/" >> "${STATS}" || true
    sleep 2
  done
) &

cleanup() { rm -f "${SENTINEL}"; }
trap cleanup EXIT

# Record what was actually under test. A benchmark that does not state the
# server configuration is a number without a meaning - and `docker compose run`
# can silently recreate the API from the ambient environment.
API_CMD="$(docker inspect -f '{{join .Config.Cmd " "}}' "$(cd "${ROOT}" && docker compose ps -q api)")"
API_PROCS="$(cd "${ROOT}" && docker compose exec -T api python -c "
import os
me = os.getpid()
needle = 'multiprocessing.' + 'spawn'
n = 0
for p in os.listdir('/proc'):
    if not p.isdigit() or int(p) == me:
        continue
    try:
        if needle in open(f'/proc/{p}/cmdline', errors='ignore').read():
            n += 1
    except OSError:
        pass
print(n)
" 2>/dev/null | tr -d '\r')"

{
  echo "# api command : ${API_CMD}"
  echo "# api workers : ${API_PROCS}"
  echo "# generator   : ${GENERATOR}"
  echo "# started     : $(date -Iseconds)"
} | tee "${RESULT}"
echo

if [ "${GENERATOR}" = "host" ]; then
  "${BIN}" -clients "${CLIENTS}" -duration "${DURATION}" ${USER_RANGE_ARGS} "$@" | tee -a "${RESULT}"
else
  (cd "${ROOT}" && docker compose --profile load run --rm load-generator \
      -clients "${CLIENTS}" -duration "${DURATION}" ${USER_RANGE_ARGS} "$@") | tee -a "${RESULT}"
fi

cleanup
wait || true

echo
echo "peak CPU and memory per container during the run"
echo "------------------------------------------------"
awk -F'\t' '
  {
    name = $2
    gsub(/%/, "", $3)
    cpu = $3 + 0
    split($4, mem, " / ")
    if (cpu > peak[name]) peak[name] = cpu
    memuse[name] = mem[1]
  }
  END {
    for (n in peak) printf "%-42s %8.1f%% cpu   %12s mem\n", n, peak[n], memuse[n]
  }
' "${STATS}" | sort

echo
echo "saved: ${RESULT}"
echo "       ${STATS}"
