"""Password handling and token issue/verify."""

import datetime as dt

import jwt
import pytest

from watchlist.modules.auth import auth_controller
from watchlist.shared.security import decode_token, hash_password, issue_token, verify_password
from watchlist.shared.settings import get_settings


class TestPasswords:
    def test_round_trip(self):
        hashed = hash_password("correct horse")
        assert hashed != "correct horse"
        assert verify_password("correct horse", hashed)

    def test_wrong_password_is_rejected(self):
        assert not verify_password("wrong", hash_password("right"))

    def test_a_malformed_hash_is_a_failed_login_not_a_crash(self):
        assert not verify_password("anything", "not-a-bcrypt-hash")

    def test_the_same_password_hashes_differently_each_time(self):
        assert hash_password("same") != hash_password("same")


class TestTokens:
    def test_round_trip(self):
        token, ttl = issue_token(42, "user1")
        claims = decode_token(token)
        assert claims["sub"] == "42"
        assert claims["username"] == "user1"
        assert ttl == get_settings().jwt_ttl_seconds

    def test_a_token_signed_with_another_secret_is_rejected(self):
        forged = jwt.encode({"sub": "1"}, "not-the-secret", algorithm="HS256")
        with pytest.raises(jwt.PyJWTError):
            decode_token(forged)

    def test_an_expired_token_is_rejected(self):
        settings = get_settings()
        past = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        expired = jwt.encode(
            {"sub": "1", "exp": int(past.timestamp())},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_token(expired)


class TestUserCreation:
    async def test_registering_creates_a_default_watchlist(self, conn):
        user = await auth_controller.create(conn, "alice", hash_password("pw"), None, "Alice", "A")
        count = await conn.fetchval(
            "SELECT count(*) FROM watchlists WHERE user_id = $1", user["id"]
        )
        assert count == 1

    async def test_default_watchlist_id_is_stable(self, conn):
        user = await auth_controller.create(conn, "bob", hash_password("pw"), None, "Bob", "B")
        first = await auth_controller.default_watchlist_id(conn, user["id"])
        second = await auth_controller.default_watchlist_id(conn, user["id"])
        assert first == second

    async def test_default_watchlist_is_created_for_a_seeded_user(self, conn):
        """Bulk-seeded users get no watchlist row; the read path creates it lazily."""
        uid = await conn.fetchval(
            "INSERT INTO users (username, password_hash) VALUES ('seeded', 'x') RETURNING id"
        )
        assert await auth_controller.default_watchlist_id(conn, uid) is not None
