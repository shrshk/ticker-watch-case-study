"""Process configuration, read once from the environment at import time."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Vendor API.
    albert_api_key: str = ""
    albert_api_base: str = "https://app.albert.com/casestudy"

    # Datastores.
    postgres_dsn: str = "postgresql://postgres:postgres@db:5432/postgres"
    redis_url: str = "redis://redis:6379/0"

    # The pool is PER PROCESS. Total connections to Postgres is
    # (uvicorn workers x db_pool_max_size) + the price service's own pool, and
    # that total must stay under the server's max_connections. Scaling workers
    # without accounting for this exhausts the database rather than adding
    # capacity - see docs/measurements.md, section 5.
    db_pool_min_size: int = 2
    db_pool_max_size: int = 16

    # Auth. The same secret signs Centrifugo connection tokens.
    jwt_secret: str = "dev-only-not-a-secret"
    jwt_ttl_seconds: int = 86_400
    jwt_algorithm: str = "HS256"

    # Price service.
    price_source: Literal["api", "simulated"] = "api"
    publish_interval_seconds: float = 5.0
    upstream_poll_interval_seconds: float = 5.0
    price_cache_ttl_seconds: int = 60

    # Simulated source.
    sim_volatility: float = 0.002
    sim_change_ratio: float = 0.30
    sim_seed: int = 1

    # Read path.
    latest_price_source: Literal["redis", "postgres"] = "redis"

    # Client-visible.
    transport: Literal["poll", "push"] = "poll"


@lru_cache
def get_settings() -> Settings:
    return Settings()
