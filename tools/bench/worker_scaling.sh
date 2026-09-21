#!/usr/bin/env bash
# Does adding uvicorn workers move the polling ceiling?
#
# For each worker count, run a short ladder and report sustained throughput,
# latency and CPU. Changing the worker count rewrites .env and recreates the
# API container, because `docker compose run` resolves depends_on services from
# .env - an inline override is silently dropped, and the run would then measure
# a different server than the one in the results.
#
#   tools/bench/worker_scaling.sh 1:4000,6000 2:8000,12000 4:20000,25000
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

DURATION="${DURATION:-45s}"
LOGICAL_USERS="${LOGICAL_USERS:-1000000}"

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

# Always rebuild the generator image. A stale image once ran an old binary
# without the flags this script passes, failed on every run, and the failure
# was swallowed - four rows of dashes and no error. Build, then refuse silence.
docker compose --profile load build load-generator >/dev/null

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

set_workers() {
  python3 - "$1" <<'PY'
import pathlib, re, sys
p = pathlib.Path(".env")
p.write_text(re.sub(r"^UVICORN_ARGS=.*$", f"UVICORN_ARGS=--workers {sys.argv[1]}",
                    p.read_text(), flags=re.M))
PY
  docker compose up -d --no-deps api >/dev/null 2>&1
  for _ in $(seq 1 40); do
    curl -fs http://localhost:8000/health >/dev/null 2>&1 && break
    sleep 1
  done
  local actual
  actual="$(docker inspect -f '{{join .Config.Cmd " "}}' "$(docker compose ps -q api)")"
  if [[ "${actual}" != *"--workers $1"* ]]; then
    echo "REFUSING: api is running '${actual}', not --workers $1" >&2
    exit 1
  fi
}

run_one() {
  local workers="$1" clients="$2"
  local sentinel="${WORK}/sampling" stats="${WORK}/stats" result="${WORK}/result"
  : > "${stats}"
  touch "${sentinel}"

  (
    while [ -e "${sentinel}" ]; do
      docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' \
        "$(docker compose ps -q api)" "$(docker compose ps -q db)" 2>/dev/null >> "${stats}" || true
      sleep 3
    done
  ) &

  docker compose --profile load run --rm load-generator \
    -clients "${clients}" -duration "${DURATION}" \
    -logical-users "${LOGICAL_USERS}" ${USER_RANGE_ARGS} > "${result}" 2> "${WORK}/stderr" || true

  rm -f "${sentinel}"
  wait 2>/dev/null || true

  if ! grep -q '^request rate' "${result}"; then
    echo "generator produced no result for workers=${workers} clients=${clients}:" >&2
    grep -vE '^ (Container|Network)' "${WORK}/stderr" "${result}" | head -20 >&2
    exit 1
  fi

  python3 - "${workers}" "${clients}" "${result}" "${stats}" <<'PY'
import re, sys

workers, clients, result_path, stats_path = sys.argv[1:5]
text = open(result_path).read()

def grab(pattern, default="-"):
    m = re.search(pattern, text)
    return m.group(1) if m else default

rate = grab(r"request rate\s+(\S+)")
errors = int(grab(r"transport errors\s+(\d+)", "0")) + int(grab(r"non-200 responses\s+(\d+)", "0"))
lat = re.search(r"request latency\s+p50 (\S+)\s+p95 (\S+)\s+p99 (\S+)", text)
p50, p95, p99 = lat.groups() if lat else ("-", "-", "-")

peak = {}
for line in open(stats_path):
    parts = line.split()
    if len(parts) == 2:
        name, cpu = parts
        key = "api" if "-api-" in name else "db" if "-db-" in name else None
        if key:
            peak[key] = max(peak.get(key, 0.0), float(cpu.rstrip("%")))

print(f"{workers:<9} {clients:<9} {rate:>10} {p50:>10} {p95:>10} {p99:>10} "
      f"{errors:>8} {peak.get('api', 0):>8.0f}% {peak.get('db', 0):>8.0f}%")
PY
}

printf '%-9s %-9s %10s %10s %10s %10s %8s %9s %9s\n' \
  workers clients req/s p50 p95 p99 errors api_cpu db_cpu
printf '%.0s-' {1..92}; echo

for spec in "$@"; do
  workers="${spec%%:*}"
  set_workers "${workers}"
  for clients in $(echo "${spec#*:}" | tr ',' ' '); do
    run_one "${workers}" "${clients}"
  done
done
