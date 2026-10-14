SHELL := /bin/bash

.PHONY: help install install-dev playwright fmt lint test migrate revision up down logs shell psql deploy

help:
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install runtime deps
	pip install -e .

install-dev: ## Install runtime + dev deps
	pip install -e ".[dev]"

playwright: ## Install Playwright browsers
	python -m playwright install --with-deps chromium

fmt: ## Ruff format + fix
	ruff format src tests
	ruff check --fix src tests

lint: ## Ruff + mypy
	ruff check src tests
	mypy src

test: ## Run tests
	pytest -q

migrate: ## Run alembic upgrade head
	alembic upgrade head

revision: ## New alembic revision: make revision m="msg"
	alembic revision --autogenerate -m "$(m)"

up: ## docker compose up
	docker compose up -d --build

down: ## docker compose down
	docker compose down

logs: ## tail compose logs
	docker compose logs -f --tail=200

shell: ## open a shell in the scraper container
	docker compose exec scraper bash

psql: ## psql shell
	docker compose exec postgres psql -U $${POSTGRES_USER:-pfh} -d $${POSTGRES_DB:-pfh}

deploy: ## run the deploy script on the current host
	bash scripts/deploy.sh
