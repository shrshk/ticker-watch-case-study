"""Create the demo users the README refers to: user1 and user2."""

import asyncio
import pathlib
import sys

import asyncpg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from watchlist.modules.auth import auth_handler  # noqa: E402
from watchlist.shared.settings import get_settings  # noqa: E402

DEMO_USERS = [
    ("user1", "password", "user1@example.com", "User", "One"),
    ("user2", "password", "user2@example.com", "User", "Two"),
]


async def main() -> None:
    conn = await asyncpg.connect(dsn=get_settings().postgres_dsn)
    try:
        for username, password, email, first, last in DEMO_USERS:
            try:
                await auth_handler.register(
                    conn,
                    username=username,
                    password=password,
                    email=email,
                    first_name=first,
                    last_name=last,
                )
            except auth_handler.UsernameTakenError:
                print(f"{username} already exists")
                continue
            print(f"created {username} (password: {password})")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
