"""Password hashing and JWT issue/verify.

Deliberately small. An OIDC provider (Keycloak et al) is where this goes in
production; see the README's deliberate-cuts section for what it would replace.
The same secret will sign Centrifugo connection tokens, so the realtime token
endpoint is a claims transform rather than a second auth system.
"""

import datetime as dt

import bcrypt
import jwt

from watchlist.shared.settings import get_settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        # Malformed hash in the row; treat as a failed login, not a 500.
        return False


def issue_token(user_id: int, username: str) -> tuple[str, int]:
    """Return (token, expires_in_seconds)."""
    settings = get_settings()
    now = dt.datetime.now(dt.UTC)
    ttl = settings.jwt_ttl_seconds
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), ttl


def decode_token(token: str) -> dict:
    """Raises jwt.PyJWTError on anything invalid."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
