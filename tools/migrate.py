"""Apply every .sql file in migrations/ in filename order. Idempotent."""

import asyncio
import pathlib
import sys

import asyncpg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from watchlist.shared.settings import get_settings  # noqa: E402

MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / "migrations"


async def main() -> None:
    conn = await asyncpg.connect(dsn=get_settings().postgres_dsn)
    try:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            print(f"applying {path.name}")
            await conn.execute(path.read_text())
        print("migrations applied")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
