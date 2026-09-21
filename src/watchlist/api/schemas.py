"""Request and response shapes for the API."""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(LoginRequest):
    email: str | None = None
    first_name: str = ""
    last_name: str = ""


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None = None
    first_name: str = ""
    last_name: str = ""


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class SecurityOut(BaseModel):
    id: int
    ticker: str
    name: str
    exchange: str | None = None


class WatchlistItemOut(SecurityOut):
    price: float | None = None
    effective_at: str | None = None
    # 'api' or 'simulated'. Surfaced so nobody watching a demo mistakes
    # generated numbers for live market data.
    source: str | None = None


class WatchlistOut(BaseModel):
    items: list[WatchlistItemOut]
    # Which path served the prices, and how well the cache did. Cheap to
    # return, and it makes the LATEST_PRICE_SOURCE comparison observable
    # from the client instead of only from the metrics.
    read_path: str
    cache_hits: int
    cache_misses: int
    as_of: str


class AddItemRequest(BaseModel):
    security_id: int
