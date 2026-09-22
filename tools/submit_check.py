"""Refuse to package a .env left in benchmark mode.

.env ships inside solution.zip, so whatever it says at `make submit` is what a
reviewer runs. The submission default is the real vendor, push transport, Redis engine, one
Centrifugo node, a single reloading API process; simulated prices, polling,
NATS, extra nodes and multiple workers are test and benchmark tooling.
Refusing beats silently rewriting the file the operator is looking at.
"""

import pathlib
import sys

SUBMISSION_DEFAULTS = {
    "PRICE_SOURCE": "api",
    "UVICORN_ARGS": "",
    "LATEST_PRICE_SOURCE": "redis",
    "TRANSPORT": "push",
    "BROKER": "redis",
}

env = pathlib.Path(".env")
if not env.exists():
    sys.exit(".env is missing. Copy .env.example.")

values = dict(
    line.split("=", 1)
    for line in env.read_text().splitlines()
    if "=" in line and not line.startswith("#")
)

for key in ("VENDOR_API_KEY", "VENDOR_API_BASE", "VENDOR_API_KEY_HEADER"):
    if not values.get(key, "").strip() or values[key] == "replace-me":
        sys.exit(f"{key} is not set in .env; the submission runs PRICE_SOURCE=api.")

drift = {
    k: (values.get(k, ""), want)
    for k, want in SUBMISSION_DEFAULTS.items()
    if values.get(k, "") != want
}
if drift:
    print("refusing to package: .env is still in test/benchmark mode", file=sys.stderr)
    for key, (have, want) in drift.items():
        print(f"  {key}={have!r}  (submission default: {want!r})", file=sys.stderr)
    print("\nfix .env, `make restart`, then `make submit` again.", file=sys.stderr)
    sys.exit(1)

print("submit-check: .env is at submission defaults (PRICE_SOURCE=api, single reloading worker)")
