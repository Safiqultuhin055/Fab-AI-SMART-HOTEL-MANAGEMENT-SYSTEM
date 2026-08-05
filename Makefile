# ASHOS developer commands.  `make help` lists everything.
# Works on Linux/macOS and on Windows under Git Bash.

PY ?= .venv/Scripts/python.exe
ifeq ($(OS),)
PY = .venv/bin/python
endif

COMPOSE = docker compose -f deploy/compose.yml --env-file .env

.DEFAULT_GOAL := help
.PHONY: help setup env run shell migrate migrations superuser seed bootstrap \
        test cov lint fmt typecheck schema up down logs ps psql reset-db check-deploy

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- environment --------------------------------------------------------------
setup: ## Create the virtualenv and install dev dependencies
	python -m venv .venv
	$(PY) -m pip install --upgrade pip wheel
	$(PY) -m pip install -r requirements/dev.txt

env: ## Create .env from the template (never overwrites an existing one)
	@test -f .env || cp .env.example .env
	@echo ".env ready — set DJANGO_SECRET_KEY and AI_LLM_API_KEY before running."

# --- django -------------------------------------------------------------------
run: ## Run the ASGI dev server
	$(PY) manage.py runserver 0.0.0.0:8000

shell: ## Django shell
	$(PY) manage.py shell

migrations: ## Create migrations
	$(PY) manage.py makemigrations

migrate: ## Apply migrations
	$(PY) manage.py migrate

superuser: ## Create an admin user
	$(PY) manage.py createsuperuser

seed: ## Seed system roles and their permissions
	$(PY) manage.py seed_roles

seed-demo: ## Seed demo hotels, staff, AI usage history and audit trail
	$(PY) manage.py seed_demo

reseed-demo: ## Wipe demo data and regenerate it (keeps real hotels)
	$(PY) manage.py seed_demo --flush

users: ## Write user.txt — every account, role and module access (git-ignored)
	$(PY) manage.py dump_users

bootstrap: migrate seed ## Fresh database ready to log into
	$(PY) manage.py bootstrap_hotel

# --- quality ------------------------------------------------------------------
test: ## Run tests (excludes ai_eval)
	$(PY) -m pytest -m "not ai_eval"

cov: ## Tests with coverage report
	$(PY) -m pytest -m "not ai_eval" --cov --cov-report=term-missing

lint: ## ruff + black check
	$(PY) -m ruff check .
	$(PY) -m black --check .

fmt: ## Autoformat and autofix
	$(PY) -m ruff check --fix .
	$(PY) -m black .

typecheck: ## mypy
	$(PY) -m mypy apps api services config

schema: ## Write the OpenAPI schema to openapi.yml
	$(PY) manage.py spectacular --file openapi.yml

check-deploy: ## Production readiness checklist
	DJANGO_SETTINGS_MODULE=config.settings.prod $(PY) manage.py check --deploy

# --- docker -------------------------------------------------------------------
up: ## Start the full stack
	$(COMPOSE) up -d --build

down: ## Stop the stack
	$(COMPOSE) down

logs: ## Tail application logs
	$(COMPOSE) logs -f web worker

ps: ## Show stack status
	$(COMPOSE) ps

psql: ## Open a psql session in the database container
	$(COMPOSE) exec postgres psql -U ashos -d ashos

reset-db: ## DESTRUCTIVE: drop the database volume and rebuild from scratch
	$(COMPOSE) down -v
	$(COMPOSE) up -d postgres redis
	@echo "volumes dropped; run 'make migrate seed' once postgres is healthy"
