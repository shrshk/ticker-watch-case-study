"""Steady-state slice of a Prometheus histogram: (end snapshot on stdin) minus (ramp snapshot file).

Centrifugo's broadcast histogram is cumulative. Subtracting the snapshot taken
when the connect ramp finished leaves only the broadcasts that happened with
every client connected - the cost of the broker at steady state, not the cost
of the machine while thousands of generators were dialling in.
"""

import re
import sys

METRIC = "centrifugo_node_broadcast_duration_seconds"


def parse(lines):
    buckets, count, total = {}, 0.0, 0.0
    for line in lines:
        if line.startswith(METRIC + "_bucket"):
            le = re.search(r'le="([^"]+)"', line).group(1).replace("+Inf", "inf")
            buckets[float(le)] = float(line.split()[-1])
        elif line.startswith(METRIC + "_count"):
            count = float(line.split()[-1])
        elif line.startswith(METRIC + "_sum"):
            total = float(line.split()[-1])
    return buckets, count, total


ramp = open(sys.argv[1]).read().splitlines() if len(sys.argv) > 1 else []
end = sys.stdin.read().splitlines()
b0, c0, s0 = parse(ramp)
b1, c1, s1 = parse(end)
count, total = c1 - c0, s1 - s0
if count <= 0:
    print("no broadcasts after ramp")
    sys.exit()
delta = {le: b1[le] - b0.get(le, 0.0) for le in b1}


def quantile(q):
    for le in sorted(delta):
        if delta[le] >= q * count:
            return le
    return float("inf")


print(
    f"count={count:.0f} mean={1000 * total / count:.2f}ms "
    f"p95<={1000 * quantile(0.95):.0f}ms p99<={1000 * quantile(0.99):.0f}ms"
)
