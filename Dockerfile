# One image, two processes. The api and price-service have identical
# dependencies and a real process boundary is what matters, not a second build.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer: cached unless pyproject/uv.lock change.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-install-project --no-dev

COPY src/ ./src/
COPY migrations/ ./migrations/
COPY tools/ ./tools/
COPY seed/ ./seed/

RUN uv sync --no-dev

EXPOSE 8000
CMD ["uvicorn", "watchlist.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
