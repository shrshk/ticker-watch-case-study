"""Dump the demo database - and refuse to dump a benchmark one.

The Postgres volume lives outside the project directory and, after the
polling measurements, holds a million load-test users. This writes the
*sensible* version: schema plus the demo state - the 99 securities,
latest_prices, user1/user2 and their watchlists - to db/demo.sql, which
Postgres loads automatically on a fresh volume via /docker-entrypoint-initdb.d.
Refresh-token rows are excluded: sessions do not ship.

Refuses if any load_user_* rows exist: a gigabyte of seeded rows in the demo
dump would be a defect.
"""

import pathlib
import subprocess
import sys

OUT = pathlib.Path(__file__).resolve().parents[1] / "db" / "demo.sql"


def psql(sql: str) -> str:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres", "-tAc", sql],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


load_users = int(psql("SELECT count(*) FROM users WHERE username LIKE 'load\\_user\\_%'"))
if load_users:
    sys.exit(
        f"refusing: {load_users:,} load-test users present. The demo dump seeds every\n"
        "fresh clone; run it against a fresh stack (make clean && make bootstrap)."
    )

sources = psql("SELECT string_agg(DISTINCT source, ',') FROM latest_prices")
if "simulated" in (sources or ""):
    print(
        f"warning: latest_prices holds simulated rows ({sources}); a fresh start with "
        "PRICE_SOURCE=api will have them wiped on first tick, which is correct but untidy.",
        file=sys.stderr,
    )

dump = subprocess.run(
    [
        "docker",
        "compose",
        "exec",
        "-T",
        "db",
        "pg_dump",
        "-U",
        "postgres",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "--exclude-table-data=refresh_tokens",
        "postgres",
    ],
    check=True,
    capture_output=True,
    text=True,
).stdout

OUT.parent.mkdir(exist_ok=True)
OUT.write_text(dump)
rows = {
    t: psql(f"SELECT count(*) FROM {t}")
    for t in ("users", "securities", "watchlists", "watchlist_items", "latest_prices")
}
print(
    f"wrote {OUT.relative_to(OUT.parents[1])} ({len(dump.encode()) / 1024:.0f} KB): "
    + ", ".join(f"{t}={n}" for t, n in rows.items())
)
