"""Request and response shapes for the auth domain."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(LoginRequest):
    email: str | None = None
    first_name: str = ""
    last_name: str = ""


class Principal(BaseModel):
    """Who is calling, straight from the verified token. No database read.

    A stateless token is trusted for its lifetime; that is the trade. Revoking
    a user before their token expires needs a denylist or a short TTL, and
    neither is built here. What this buys is one fewer query on every request,
    which at 5,000 polls per second is not a rounding error.
    """

    id: int
    username: str


class User(BaseModel):
    id: int
    username: str
    email: str | None = None
    first_name: str = ""
    last_name: str = ""


class Session(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: User
