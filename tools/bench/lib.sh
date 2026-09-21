# Shared pieces for the phase-3 scenario harnesses. Source, do not run.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"
DURATION="${DURATION:-45s}"
LOGICAL_USERS="${LOGICAL_USERS:-1000000}"
COMPOSE_PUSH="docker compose --profile push --profile load"

user_range() {
  local lo hi
  lo="$(docker compose exec -T db psql -U postgres -tAc "SELECT min(id) FROM users WHERE username LIKE 'load\_user\_%'" | tr -d '[:space:]\r')"
  hi="$(docker compose exec -T db psql -U postgres -tAc "SELECT max(id) FROM users WHERE username LIKE 'load\_user\_%'" | tr -d '[:space:]\r')"
  [ -n "${lo}" ] || { echo "no seeded load users; make seed-small first" >&2; exit 1; }
  echo "-user-id-min ${lo} -user-id-max ${hi}"
}

set_env() {  # KEY VALUE - rewrite one .env line
  python3 - "$1" "$2" <<'PY'
import pathlib, re, sys
k, v = sys.argv[1:3]
p = pathlib.Path(".env"); s = p.read_text()
s = re.sub(rf"^{k}=.*$", f"{k}={v}", s, flags=re.M) if re.search(rf"^{k}=", s, re.M) else s + f"\n{k}={v}\n"
p.write_text(s)
PY
}

recreate() {  # services... - force-recreate so .env and code both apply
  ${COMPOSE_PUSH} up -d --force-recreate --no-deps "$@" >/dev/null 2>&1
}

wait_healthy() {  # service - poll compose health
  for _ in $(seq 1 40); do
    local st; st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}' "$(${COMPOSE_PUSH} ps -q "$1" 2>/dev/null)" 2>/dev/null || true)"
    [ "${st}" = "healthy" ] || [ "${st}" = "running" ] && return 0
    sleep 1
  done
  echo "REFUSING: $1 did not become healthy" >&2; exit 1
}

assert_env() {  # service KEY VALUE - prove the container runs what the results will claim
  local got; got="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$(${COMPOSE_PUSH} ps -q "$1")" | sed -n "s/^$2=//p")"
  [ "${got}" = "$3" ] || { echo "REFUSING: $1 has $2=${got}, wanted $3" >&2; exit 1; }
}

cf_metric_sum() {  # metric [label-substring] - sum of a Centrifugo counter
  curl -s http://localhost:8001/metrics | awk -v m="$1" -v f="${2:-}" '$0 ~ "^"m"[{ ]" && (f=="" || index($0,f)) {s+=$2} END {print s+0}'
}

cf_histogram() {  # metric - print count, sum, and the p50/p95/p99 bucket upper bounds
  curl -s http://localhost:8001/metrics | python3 -c '
import sys,re
m=sys.argv[1]; b=[]; c=s=0
for l in sys.stdin:
    if l.startswith(m+"_bucket"):
        le=float(re.search(r"le=\"([^\"]+)\"",l).group(1).replace("+Inf","inf")); b.append((le,float(l.split()[-1])))
    elif l.startswith(m+"_count"): c=float(l.split()[-1])
    elif l.startswith(m+"_sum"): s=float(l.split()[-1])
def q(p):
    for le,n in sorted(b):
        if n>=p*c: return le
    return float("inf")
print(f"count={c:.0f} mean={1000*s/c if c else 0:.2f}ms p50<={1000*q(.5):.1f}ms p95<={1000*q(.95):.1f}ms p99<={1000*q(.99):.1f}ms")
' "$1"
}

run_gen() {  # args... - run the generator, print its report, fail loudly on silence
  local out err; err="$(mktemp)"
  out="$(${COMPOSE_PUSH} run --rm load-generator "$@" 2> "${err}")" || true
  if ! grep -qE '^(request rate|publications recv)' <<<"${out}"; then
    echo "generator produced no result:" >&2; grep -vE '^ (Container|Network)' "${err}" | head -12 >&2; rm -f "${err}"; exit 1
  fi
  rm -f "${err}"; echo "${out}"
}

field() { sed -n "s/^$1 *//p" <<<"$2" | head -1; }
