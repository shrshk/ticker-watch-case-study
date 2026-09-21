"""Short-lived access tokens, rotated refresh tokens.

The access token is verified from its signature alone, so it must be short.
The refresh token is the only place revocation lives, so these tests are about
what it refuses.
"""

import datetime as dt

import pytest

from watchlist.modules.auth import auth_controller, auth_handler
from watchlist.shared.security import decode_token, hash_refresh_token
from watchlist.shared.settings import get_settings


@pytest.fixture
async def session(conn):
    return await auth_handler.register(conn, "refresh_user", "pw")


async def test_login_issues_both_tokens(session):
    assert session.access_token
    assert session.refresh_token
    assert session.expires_in == get_settings().access_token_ttl_seconds
    assert session.expires_in <= 15 * 60, "access tokens must be short; refresh is what lasts"


async def test_the_refresh_token_is_stored_hashed_not_plain(conn, session):
    stored = await conn.fetchval(
        "SELECT token_hash FROM refresh_tokens WHERE user_id = $1", session.user.id
    )
    assert stored != session.refresh_token
    assert stored == hash_refresh_token(session.refresh_token)


async def test_refresh_returns_a_new_pair_and_rotates(conn, session):
    renewed = await auth_handler.refresh(conn, session.refresh_token)

    assert renewed.refresh_token != session.refresh_token
    assert decode_token(renewed.access_token)["sub"] == str(session.user.id)

    old = await auth_controller.get_refresh_token(conn, hash_refresh_token(session.refresh_token))
    assert old["revoked_at"] is not None, "the presented token must be retired on use"


async def test_reusing_a_rotated_token_revokes_every_session(conn, session):
    renewed = await auth_handler.refresh(conn, session.refresh_token)

    # Replay the token that was just rotated away.
    with pytest.raises(auth_handler.InvalidRefreshTokenError):
        await auth_handler.refresh(conn, session.refresh_token)

    # The legitimate successor is now dead too - the standard response to a
    # token being presented twice, because one of the two holders is not you.
    with pytest.raises(auth_handler.InvalidRefreshTokenError):
        await auth_handler.refresh(conn, renewed.refresh_token)

    live = await conn.fetchval(
        "SELECT count(*) FROM refresh_tokens WHERE user_id = $1 AND revoked_at IS NULL",
        session.user.id,
    )
    assert live == 0


async def test_an_unknown_token_is_rejected(conn):
    with pytest.raises(auth_handler.InvalidRefreshTokenError):
        await auth_handler.refresh(conn, "not-a-real-token-at-all")


async def test_an_expired_token_is_rejected(conn, session):
    await conn.execute(
        "UPDATE refresh_tokens SET expires_at = $1 WHERE user_id = $2",
        dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1),
        session.user.id,
    )
    with pytest.raises(auth_handler.InvalidRefreshTokenError):
        await auth_handler.refresh(conn, session.refresh_token)


async def test_logout_retires_the_token(conn, session):
    await auth_handler.logout(conn, session.refresh_token)
    with pytest.raises(auth_handler.InvalidRefreshTokenError):
        await auth_handler.refresh(conn, session.refresh_token)


async def test_logout_with_an_unknown_token_is_not_an_error(conn):
    await auth_handler.logout(conn, "never-issued")


async def test_deleting_the_user_ends_the_session_at_the_next_refresh(conn, session):
    """This is the revocation guarantee C4 traded away and refresh buys back:
    the access token stays valid for its TTL, then the refresh fails."""
    await conn.execute("DELETE FROM users WHERE id = $1", session.user.id)
    with pytest.raises(auth_handler.InvalidRefreshTokenError):
        await auth_handler.refresh(conn, session.refresh_token)


async def test_each_login_gets_its_own_refresh_token(conn):
    await auth_handler.register(conn, "multi", "pw")
    first = await auth_handler.login(conn, "multi", "pw")
    second = await auth_handler.login(conn, "multi", "pw")

    assert first.refresh_token != second.refresh_token
    # Logging out one device does not log out the other.
    await auth_handler.logout(conn, first.refresh_token)
    renewed = await auth_handler.refresh(conn, second.refresh_token)
    assert renewed.user.username == "multi"
