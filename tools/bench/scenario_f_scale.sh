#!/usr/bin/env bash
# Scenario F at scale: extend the fanout curve past what one generator can hold.
# N generator containers run in parallel, each with CLIENTS/N connections, all
# subscribing to the celebrity ticker. The metric that matters is Centrifugo's
# own broadcast-duration histogram (global, reset by recreating the container
# before each run); client-side latency is shown per generator because
# percentiles cannot be merged across them. Total VM CPU is recorded so an
# environment ceiling is not misread as a broker one.
#
#   tools/bench/scenario_f_scale.sh 50000 2
#   tools/bench/scenario_f_scale.sh 100000 4
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_simulated
CLIENTS="${1:?clients}"; N="${2:?generator containers}"
TICKER="${TICKER:-NVDA}"
RAMP="${RAMP:-30s}"
NODES="${NODES:-1}"   # Centrifugo nodes; >1 adds docker-compose.nodes.yml and pins generators round-robin
RANGE="$(user_range)"
PER=$(( CLIENTS / N ))
if [ "${NODES}" -gt 1 ]; then
  COMPOSE_PUSH="docker compose -f docker-compose.yml -f docker-compose.nodes.yml --profile push --profile load"
fi
${COMPOSE_PUSH} build load-generator >/dev/null
set_env TRANSPORT push; recreate price-service; wait_healthy price-service
node_services=(centrifugo)
# `seq 2 1` counts DOWN and yields "2 1" - which made NODES=1 try to recreate
# centrifugo-2 and centrifugo-1 and fail. Only extend the list past node 1.
if [ "${NODES}" -gt 1 ]; then for k in $(seq 2 "${NODES}"); do node_services+=("centrifugo-${k}"); done; fi
recreate "${node_services[@]}"; for svc in "${node_services[@]}"; do wait_healthy "${svc}"; done
if [ "${NODES}" -gt 1 ]; then
  sleep 12  # node discovery via the engine is periodic
  for svc in "${node_services[@]}"; do
    seen="$(${COMPOSE_PUSH} exec -T "${svc}" wget -qO- http://localhost:8000/metrics 2>/dev/null | awk '/^centrifugo_node_num_nodes /{print $2}')"
    [ "${seen}" = "${NODES}" ] || { echo "REFUSING: ${svc} sees ${seen} nodes, wanted ${NODES}" >&2; exit 1; }
  done
fi

node_url() {  # generator index -> ws url of its pinned node, round-robin
  local k=$(( ($1 - 1) % NODES + 1 ))
  if [ "${k}" -eq 1 ]; then echo "ws://centrifugo:8000/connection/websocket"; else echo "ws://centrifugo-${k}:8000/connection/websocket"; fi
}
node_metrics() { ${COMPOSE_PUSH} exec -T "$1" wget -qO- http://localhost:8000/metrics 2>/dev/null; }

echo "clients=${CLIENTS} across ${N} generators (${PER} each) on ${NODES} centrifugo node(s), ramp=${RAMP}, duration=${DURATION}, celebrity=${TICKER}"
stats="$(mktemp)"; sentinel="$(mktemp)"; outdir="$(mktemp -d)"
( while [ -e "${sentinel}" ]; do
    docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' 2>/dev/null >> "${stats}" || true
    sleep 3
  done ) &

# Histogram snapshots: cumulative at ramp-end and at run-end. The steady-state
# delta is the number that says what the broker costs once everyone is
# connected; the cumulative figure is dominated by the ramp whenever the
# generators saturate the machine while connecting.
hist_snap() { node_metrics "$1" | grep -E "^centrifugo_node_broadcast_duration_seconds_(bucket|count|sum)"; }
( sleep "$(( ${RAMP%s} + 8 ))"; for svc in "${node_services[@]}"; do hist_snap "${svc}" > "${outdir}/ramp_${svc}.hist"; done ) &
MEASURE_AFTER="$(( ${RAMP%s} + 8 ))s"

pids=()
for i in $(seq 1 "${N}"); do
  # --no-deps: N concurrent `compose run`s otherwise all try to ensure the
  # api's depends_on and race on recreating it - the second one died with a
  # container-name conflict, and a "50k" run silently became a 25k run.
  ( ${COMPOSE_PUSH} run --rm --no-deps -e CENTRIFUGO_URL="$(node_url "${i}")" load-generator -transport push -clients "${PER}" -duration "${DURATION}" \
      -ramp-up "${RAMP}" -measure-after "${MEASURE_AFTER}" -celebrity "${TICKER}" -logical-users "${LOGICAL_USERS}" ${RANGE} \
      > "${outdir}/gen${i}.out" 2> "${outdir}/gen${i}.err" || true ) &
  pids+=($!)
done
for p in "${pids[@]}"; do wait "${p}" || true; done
rm -f "${sentinel}"; wait 2>/dev/null || true

echo; echo "--- per generator (client-side) ---"
printf '%-5s %9s %9s %11s %-44s %-44s\n' gen connects subs cf_errors all_update_latency celebrity_latency
for i in $(seq 1 "${N}"); do
  o="${outdir}/gen${i}.out"
  if ! grep -q '^publications recv' "${o}"; then echo "gen${i}: NO RESULT"; grep -vE '^ (Container|Network)' "${outdir}/gen${i}.err" | head -5; continue; fi
  printf '%-5s %9s %9s %11s %-44s %-44s\n' "gen${i}" "$(field connects "$(cat "$o")")" "$(field subscriptions "$(cat "$o")")" "$(field 'realtime errors' "$(cat "$o")")" \
    "$(sed -n 's/^update latency *\(p50 [0-9]*ms *p95 [0-9]*ms *p99 [0-9]*ms\).*/\1/p' "$o")" \
    "$(sed -n 's/^celebrity [A-Z.]* *[0-9]* messages; update latency \(p50 [0-9]*ms p95 [0-9]*ms p99 [0-9]*ms\).*/\1/p' "$o")"
done

echo; echo "--- centrifugo (authoritative), per node ---"
for svc in "${node_services[@]}"; do
  mtx="$(node_metrics "${svc}")"
  clients="$(awk '/^centrifugo_node_num_clients /{print $2}' <<<"${mtx}")"
  subs="$(awk '/^centrifugo_node_num_subscriptions /{print $2}' <<<"${mtx}")"
  hist="$(python3 -c '
import sys,re
m="centrifugo_node_broadcast_duration_seconds"; b=[]; c=s=0
for l in sys.stdin:
    if l.startswith(m+"_bucket"):
        le=float(re.search(r"le=\"([^\"]+)\"",l).group(1).replace("+Inf","inf")); b.append((le,float(l.split()[-1])))
    elif l.startswith(m+"_count"): c=float(l.split()[-1])
    elif l.startswith(m+"_sum"): s=float(l.split()[-1])
def q(p):
    for le,n in sorted(b):
        if n>=p*c: return le
    return float("inf")
print(f"count={c:.0f} mean={1000*s/c if c else 0:.2f}ms p95<={1000*q(.95):.0f}ms p99<={1000*q(.99):.0f}ms")
' <<<"${mtx}")"
  steady="$(python3 tools/bench/hist_delta.py "${outdir}/ramp_${svc}.hist" <<<"${mtx}" 2>/dev/null || echo 'n/a')"
  printf '  %-13s clients(now)=%-7s subs(now)=%-8s\n' "${svc}" "${clients}" "${subs}"
  printf '  %-13s   whole run:    %s\n' "" "${hist}"
  printf '  %-13s   steady-state: %s   <- after ramp+8s; the number that matters\n' "" "${steady}"
done
python3 - "${stats}" "${CLIENTS}" <<'PY'
import sys, collections
stats, clients = sys.argv[1], int(sys.argv[2])
peak, series, mem = {}, collections.defaultdict(list), {}
for line in open(stats):
    parts = line.split()
    if len(parts) < 3: continue
    name, cpu = parts[0], float(parts[1].rstrip('%'))
    if 'load-generator' in name: key = 'generator'
    elif '-centrifugo-2-' in name: key = 'centrifugo-2'
    elif '-centrifugo-3-' in name: key = 'centrifugo-3'
    elif '-centrifugo-' in name: key = 'centrifugo'
    else: key = next((k for k in ('api','db','redis','price-service') if f'-{k}-' in name), None)
    if not key: continue
    series[key].append(cpu); peak[key] = max(peak.get(key, 0), cpu); mem[key] = parts[2]
def steady(v): h = v[len(v)//2:]; return sum(h)/max(1,len(h))
tot_peak = 0.0; print("cpu (peak / steady-state mean of 2nd half):")
for k in ('centrifugo','centrifugo-2','centrifugo-3','api','db','redis','price-service','generator'):
    if k in series:
        # generator containers are several; sum them per sample is not available, so report per-sample peak and note count
        print(f"  {k:<14} {peak[k]:>6.0f}% / {steady(series[k]):>6.0f}%   mem {mem[k]}")
allcpu = sum(peak[k] for k in peak)
print(f"  sum of peaks   {allcpu:>6.0f}%  of 1000% (10 vCPU) - NOTE: generator row is per-container; multiply by the container count")
PY
rm -rf "${outdir}" "${stats}"
