#!/usr/bin/env bash
# Scenario B: identical load under both transports, side by side.
#
# For each client count: run polling with the price service NOT publishing
# (TRANSPORT=poll), then push with it publishing (TRANSPORT=push). Same
# duration, same seeded users, same price movement source. Records request
# rate, update latency at the client, per-service CPU, and - for push - the
# bytes Centrifugo actually sent to clients, from its own counters.
#
#   tools/bench/scenario_b.sh 5000 10000 20000
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"
DURATION="${DURATION:-45s}"
LOGICAL_USERS="${LOGICAL_USERS:-1000000}"
WORK="$(mktemp -d)"; trap 'rm -rf "${WORK}"' EXIT
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_simulated

COMPOSE="docker compose --profile push --profile load"

USER_MIN="$(docker compose exec -T db psql -U postgres -tAc "SELECT min(id) FROM users WHERE username LIKE 'load\_user\_%'" | tr -d '[:space:]\r')"
USER_MAX="$(docker compose exec -T db psql -U postgres -tAc "SELECT max(id) FROM users WHERE username LIKE 'load\_user\_%'" | tr -d '[:space:]\r')"
[ -n "${USER_MIN}" ] || { echo "no seeded load users" >&2; exit 1; }
RANGE="-user-id-min ${USER_MIN} -user-id-max ${USER_MAX}"

${COMPOSE} build load-generator >/dev/null

set_transport() {  # poll | push - the price service publishes only under push
  python3 - "$1" <<'PY'
import pathlib, re, sys
p = pathlib.Path(".env")
p.write_text(re.sub(r"^TRANSPORT=.*$", f"TRANSPORT={sys.argv[1]}", p.read_text(), flags=re.M))
PY
  ${COMPOSE} up -d --force-recreate --no-deps price-service >/dev/null 2>&1
  sleep 8
  local got
  got="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$(docker compose ps -q price-service)" | sed -n 's/^TRANSPORT=//p')"
  [ "${got}" = "$1" ] || { echo "REFUSING: price-service has TRANSPORT=${got}, wanted $1" >&2; exit 1; }
}

cf_metric() { curl -s http://localhost:8001/metrics | awk -v m="$1" '$0 ~ "^"m"\\{" && $0 ~ /push_publication/ {s+=$2} END {print s+0}'; }

run_one() {  # transport clients
  local transport="$1" clients="$2"
  local sentinel="${WORK}/s" stats="${WORK}/stats" result="${WORK}/result"
  : > "${stats}"; touch "${sentinel}"
  local bytes0 frames0; bytes0="$(cf_metric centrifugo_transport_messages_sent_size)"; frames0="$(cf_metric centrifugo_transport_messages_sent)"

  ( while [ -e "${sentinel}" ]; do
      docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' $(docker compose --profile push ps -q) 2>/dev/null >> "${stats}" || true
      sleep 3
    done ) &

  ${COMPOSE} run --rm load-generator -transport "${transport}" -clients "${clients}" \
      -duration "${DURATION}" -logical-users "${LOGICAL_USERS}" ${RANGE} > "${result}" 2> "${WORK}/err" || true
  rm -f "${sentinel}"; wait 2>/dev/null || true

  if ! grep -qE '^(request rate|publications recv)' "${result}"; then
    echo "generator produced no result for ${transport}/${clients}:" >&2
    grep -vE '^ (Container|Network)' "${WORK}/err" "${result}" | head -12 >&2; exit 1
  fi
  local bytes1 frames1; bytes1="$(cf_metric centrifugo_transport_messages_sent_size)"; frames1="$(cf_metric centrifugo_transport_messages_sent)"

  python3 - "${transport}" "${clients}" "${result}" "${stats}" "$((bytes1-bytes0))" "$((frames1-frames0))" "${DURATION%s}" <<'PY'
import re, sys
transport, clients, result, stats, cf_bytes, cf_frames, dur = sys.argv[1:8]
t = open(result).read()
g = lambda pat, d="-": (re.search(pat, t, re.M) or [None, d])[1] if re.search(pat, t, re.M) else d
rate = g(r"^request rate\s+([\d.]+)/s")
upd = re.search(r"^update latency\s+p50 (\d+)ms\s+p95 (\d+)ms\s+p99 (\d+)ms", t, re.M)
p50, p95, p99 = upd.groups() if upd else ("-", "-", "-")
errs = int(g(r"^(?:transport errors|realtime errors)\s+(\d+)", "0")) + int(g(r"^non-200 (?:responses|snapshots)\s+(\d+)", "0"))
# Peak hides a distinction that matters: under push the API's peak is the
# connect ramp (every client takes one snapshot), not steady state. Report
# both - peak, and the mean over the second half of the run.
peak, series = {}, {}
for line in open(stats):
    parts = line.split()
    if len(parts) == 2:
        for key in ("api", "db", "redis", "centrifugo"):
            if f"-{key}-" in parts[0]:
                v = float(parts[1].rstrip("%"))
                peak[key] = max(peak.get(key, 0.0), v)
                series.setdefault(key, []).append(v)
steady = {k: (sum(v[len(v)//2:]) / max(1, len(v[len(v)//2:]))) for k, v in series.items()}
if transport == "poll":
    kb_total = float(g(r"^bytes on the wire\s+([\d.]+) KB", "0"))
else:
    kb_total = int(cf_bytes) / 1024 + float(g(r"^bytes on the wire\s+([\d.]+) KB", "0"))
per_client_min = kb_total * 1024 / int(clients) / (int(dur) / 60)
print(f"{transport:<6} {clients:>7} {rate:>8}/s {p50:>7}ms {p95:>7}ms {p99:>7}ms {errs:>6} "
      f"{peak.get('api',0):>4.0f}/{steady.get('api',0):<4.0f} {peak.get('db',0):>4.0f}/{steady.get('db',0):<4.0f} "
      f"{peak.get('redis',0):>3.0f}/{steady.get('redis',0):<3.0f} {peak.get('centrifugo',0):>4.0f}/{steady.get('centrifugo',0):<4.0f} "
      f"{per_client_min:>9.0f}")
PY
}

echo 'cpu columns are peak/steady-state-mean (second half of the run)'
printf '%-6s %7s %10s %9s %9s %9s %6s %9s %9s %7s %9s %9s\n' transport clients http_req upd_p50 upd_p95 upd_p99 errors api db redis cfugo B/client/min
printf '%.0s-' {1..108}; echo
for clients in "$@"; do
  set_transport poll;  run_one poll "${clients}"
  set_transport push;  run_one push "${clients}"
done
