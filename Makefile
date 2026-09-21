.DEFAULT_GOAL := help
.PHONY: help build up down restart logs ps sh migrate createusers createsuperuser \
        capture-prices reset-prices psql redis-cli open-app open-api test lint format submit-check \
        db-dump db-restore \
        seed-small seed-medium seed-million seed-clear db-bench load load-container \
        clean submit bootstrap

# Anything below can be overridden inline, e.g. `make up PRICE_SOURCE=simulated`.
# `make up TRANSPORT=push` adds Centrifugo; `BROKER=nats` swaps its broker.
# Both are read from .env when not given inline, so a stack started one way
# is recreated the same way by `make restart`.
# Defaults: push transport, Redis engine, one Centrifugo node. Polling and
# NATS remain as toggles for the comparison, not as alternatives to choose.
TRANSPORT ?= $(or $(shell sed -n 's/^TRANSPORT=//p' .env 2>/dev/null),push)
BROKER    ?= $(or $(shell sed -n 's/^BROKER=//p' .env 2>/dev/null),redis)
NODES     ?= 1
PROFILES  := $(if $(filter push,$(TRANSPORT)),--profile push,)
OVERRIDES := -f docker-compose.yml \
             $(if $(filter nats,$(BROKER)),-f docker-compose.nats.yml,) \
             $(if $(filter-out 1,$(NODES)),-f docker-compose.nodes.yml,)
COMPOSE := TRANSPORT=$(TRANSPORT) docker compose $(OVERRIDES) $(PROFILES)
RUN_PY  := $(COMPOSE) run --rm --no-deps -T api

# -- lifecycle ---------------------------------------------------------------

bootstrap: build up-detached migrate createusers ## Build, start, migrate and create demo users in one go
	@echo ""
	@echo "Ready. Open http://localhost:3000 and log in as user1 / password"

build: ## Build the Docker images
	$(COMPOSE) build

up: ## Run the whole stack in the foreground
	$(COMPOSE) up

up-detached: ## Run the whole stack in the background
	$(COMPOSE) up -d --wait

down: ## Stop the stack and remove containers
	$(COMPOSE) down

restart: ## Recreate services so .env AND code changes take effect
	# Not `compose restart`: that reuses each container's existing config and
	# silently ignores an edited .env. Not plain `up -d` either: that only
	# recreates on a *config* change, so a code edit under the bind mount with
	# --workers (no --reload) keeps running the old code while reporting
	# healthy. --force-recreate is slower and always right. --renew-anon-volumes
	# because the client's /app/node_modules is an anonymous volume that
	# otherwise outlives the image it came from - a new dependency in the image
	# stays invisible to Vite until the volume is renewed.
	# --build because a new dependency in pyproject/package.json is invisible
	# to a bind-mounted container until its image is rebuilt: the metrics
	# change crash-looped the price service on ModuleNotFoundError.
	$(COMPOSE) up -d --build --force-recreate --renew-anon-volumes

logs: ## Tail logs from every service
	$(COMPOSE) logs -f

ps: ## Show service status
	$(COMPOSE) ps

# -- database ----------------------------------------------------------------

migrate: ## Apply migrations/*.sql (idempotent)
	$(COMPOSE) run --rm -T api python tools/migrate.py

createusers: ## Create demo users user1 and user2, password 'password'
	$(COMPOSE) run --rm -T api python tools/create_users.py

db-dump: ## Write db/demo.sql (schema + demo state; refuses if load-test users exist)
	python3 tools/db_dump.py

db-restore: ## Load db/demo.sql into the running database (destructive: --clean)
	$(COMPOSE) exec -T db psql -U postgres -q postgres < db/demo.sql
	@echo "restored db/demo.sql"

createsuperuser: createusers ## Alias kept for parity with the original scaffold
	@echo "No Django admin in this stack; use the API or 'make psql'."

psql: ## Open a Postgres shell
	$(COMPOSE) exec db psql -U postgres postgres

redis-cli: ## Open a Redis shell
	$(COMPOSE) exec redis redis-cli

# -- prices ------------------------------------------------------------------

capture-prices: ## One-off: refresh seed/securities.csv and seed/prices.csv from the vendor
	$(COMPOSE) run --rm -T -v $(PWD)/seed:/app/seed api python tools/seed/capture_prices.py

reset-prices: ## Clear latest_prices and price:* (manual; switching PRICE_SOURCE no longer needs it)
	$(COMPOSE) run --rm -T api python tools/reset_prices.py

# -- load seeds --------------------------------------------------------------

seed-small: ## Seed 10k users / ~100k watchlist rows
	$(COMPOSE) run --rm -T api python tools/seed/seed_users.py small $(if $(TRUNCATE),--truncate)

seed-medium: ## Seed 100k users / ~1M watchlist rows
	$(COMPOSE) run --rm -T api python tools/seed/seed_users.py medium $(if $(TRUNCATE),--truncate)

seed-million: ## Seed 1M users / ~10M watchlist rows (TRUNCATE=1 to reseed from scratch)
	$(COMPOSE) run --rm -T api python tools/seed/seed_users.py million $(if $(TRUNCATE),--truncate)

seed-clear: ## Remove every seeded load user (leaves demo users and securities)
	$(COMPOSE) run --rm -T api python tools/seed/seed_users.py small --truncate --securities 0 || true

db-bench: ## Measure search, membership and snapshot latency at the current size
	$(COMPOSE) run --rm -T api python tools/bench/db_bench.py

# -- load -------------------------------------------------------------------
# Never run as part of 'make up'. Every run checks the stack is healthy first
# and refuses otherwise: numbers from a half-started stack look real and are not.

load: ## Run the load generator on the host, e.g. make load CLIENTS=5000 DURATION=120s
	tools/bench/run_load.sh $(or $(CLIENTS),1000) $(or $(DURATION),60s) \
	    -logical-users $(or $(LOGICAL_USERS),1000000)

load-container: ## Same load, generated from inside a container (see README, question 9)
	GENERATOR=container tools/bench/run_load.sh $(or $(CLIENTS),1000) $(or $(DURATION),60s) \
	    -logical-users $(or $(LOGICAL_USERS),1000000)

# -- shells and browsers -----------------------------------------------------

sh: ## Shell inside the api image
	$(COMPOSE) run --rm api bash

open-app: ## Open the client
	open http://localhost:3000

open-api: ## Open the API docs
	open http://localhost:8000/docs

# -- quality -----------------------------------------------------------------

test: ## Run the test suite
	uv run pytest

lint: ## Lint
	uv run ruff check src tools tests

format: ## Format
	uv run ruff format src tools tests
	uv run ruff check --fix src tools tests

# -- housekeeping ------------------------------------------------------------

clean: ## Stop the stack and delete its volumes
	$(COMPOSE) down -v

submit-check: ## Refuse to package a .env left in test/benchmark mode
	python3 tools/submit_check.py

submit: submit-check ## Package the project into solution.zip (runs submit-check first)
	rm -f solution.zip
	zip -r solution.zip . \
	    -x '*.git/*' '*node_modules/*' '*__pycache__/*' '*.venv/*' \
	       '*.pytest_cache/*' '*.ruff_cache/*' '*.idea/*' 'solution.zip'

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
