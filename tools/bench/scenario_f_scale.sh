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
RANGE="$(user_range)"
PER=$(( CLIENTS / N ))
${COMPOSE_PUSH} build load-generator >/dev/null
set_env TRANSPORT push; recreate price-service; wait_healthy price-service
recreate centrifugo; wait_healthy centrifugo

echo "clients=${CLIENTS} across ${N} generators (${PER} each), ramp=${RAMP}, duration=${DURATION}, celebrity=${TICKER}"
stats="$(mktemp)"; sentinel="$(mktemp)"; outdir="$(mktemp -d)"
( while [ -e "${sentinel}" ]; do
    docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' 2>/dev/null >> "${stats}" || true
    sleep 3
  done ) &

pids=()
for i in $(seq 1 "${N}"); do
  # --no-deps: N concurrent `compose run`s otherwise all try to ensure the
  # api's depends_on and race on recreating it - the second one died with a
  # container-name conflict, and a "50k" run silently became a 25k run.
  ( ${COMPOSE_PUSH} run --rm --no-deps load-generator -transport push -clients "${PER}" -duration "${DURATION}" \
      -ramp-up "${RAMP}" -celebrity "${TICKER}" -logical-users "${LOGICAL_USERS}" ${RANGE} \
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

echo; echo "--- centrifugo (authoritative) ---"
echo "connections now: $(cf_metric_sum centrifugo_node_num_clients)   subscriptions: $(cf_metric_sum centrifugo_node_num_subscriptions)"
echo "broadcast duration: $(cf_histogram centrifugo_node_broadcast_duration_seconds)"
python3 - "${stats}" "${CLIENTS}" <<'PY'
import sys, collections
stats, clients = sys.argv[1], int(sys.argv[2])
peak, series, mem = {}, collections.defaultdict(list), {}
for line in open(stats):
    parts = line.split()
    if len(parts) < 3: continue
    name, cpu = parts[0], float(parts[1].rstrip('%'))
    key = ('generator' if 'load-generator' in name else next((k for k in ('api','db','redis','centrifugo','price-service') if f'-{k}-' in name), None))
    if not key: continue
    series[key].append(cpu); peak[key] = max(peak.get(key, 0), cpu); mem[key] = parts[2]
def steady(v): h = v[len(v)//2:]; return sum(h)/max(1,len(h))
tot_peak = 0.0; print("cpu (peak / steady-state mean of 2nd half):")
for k in ('centrifugo','api','db','redis','price-service','generator'):
    if k in series:
        # generator containers are several; sum them per sample is not available, so report per-sample peak and note count
        print(f"  {k:<14} {peak[k]:>6.0f}% / {steady(series[k]):>6.0f}%   mem {mem[k]}")
allcpu = sum(peak[k] for k in peak)
print(f"  sum of peaks   {allcpu:>6.0f}%  of 1000% (10 vCPU) - NOTE: generator row is per-container; multiply by the container count")
PY
rm -rf "${outdir}" "${stats}"
