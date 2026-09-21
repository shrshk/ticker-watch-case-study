"""Create the demo users the case study README refers to: user1 and user2."""

import asyncio
import pathlib
import sys

import asyncpg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from watchlist.shared.repo import users as user_repo  # noqa: E402
from watchlist.shared.security import hash_password  # noqa: E402
from watchlist.shared.settings import get_settings  # noqa: E402

DEMO_USERS = [
    ("user1", "password", "user1@casestudy.com", "User", "One"),
    ("user2", "password", "user2@casestudy.com", "User", "Two"),
]


async def main() -> None:
    conn = await asyncpg.connect(dsn=get_settings().postgres_dsn)
    try:
        for username, password, email, first, last in DEMO_USERS:
            if await user_repo.get_by_username(conn, username):
                print(f"{username} already exists")
                continue
            await user_repo.create(conn, username, hash_password(password), email, first, last)
            print(f"created {username} (password: {password})")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
