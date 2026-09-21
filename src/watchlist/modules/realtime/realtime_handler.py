"""Connection tokens for Centrifugo.

A claims transform, not a second auth system: the token is an HS256 JWT signed
with the same secret as the access token, so Centrifugo verifies it with one
config line. `sub` is the user id. Channels are not user-private, so no
per-channel subscription tokens are needed - any authenticated connection may
subscribe to `ticker:*`.
"""

import datetime as dt

import jwt

from watchlist.modules.realtime.realtime_schema import ConnectionToken
from watchlist.shared.settings import get_settings


def connection_token(user_id: int) -> ConnectionToken:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    ttl = settings.centrifugo_token_ttl_seconds
    token = jwt.encode(
        {
            "sub": str(user_id),
            "iat": int(now.timestamp()),
            "exp": int((now + dt.timedelta(seconds=ttl)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return ConnectionToken(token=token, expires_in=ttl, channel_prefix="ticker:")
