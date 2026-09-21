"""Centrifugo connection tokens are a claims transform of the access token."""

import jwt

from watchlist.modules.realtime import realtime_handler
from watchlist.shared.settings import get_settings


def test_token_is_signed_with_the_shared_secret_and_carries_the_user():
    settings = get_settings()
    out = realtime_handler.connection_token(42)

    claims = jwt.decode(out.token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert claims["sub"] == "42", "Centrifugo reads the user id from sub"
    assert claims["exp"] - claims["iat"] == settings.centrifugo_token_ttl_seconds
    assert out.expires_in == settings.centrifugo_token_ttl_seconds


def test_channel_prefix_is_per_ticker_not_per_user():
    assert realtime_handler.connection_token(1).channel_prefix == "ticker:"
