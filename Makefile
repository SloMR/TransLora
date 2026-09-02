# Every command CI runs lives here, so ci.yml and a local checkout cannot drift.

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

# Pinned here and fetched on demand by uv, so neither is a project dependency.
RUFF ?= uvx ruff@0.16.5
MYPY ?= uvx --with httpx --with pysubs2 --with pytest mypy@2.3.1
# Prefix for python commands in cli/; pass PY= for your own venv (CI's pip leg does).
PY   ?= uv run
TAG   ?= local
LIMIT ?= 5

.DEFAULT_GOAL := help

.PHONY: help install install-cli install-web lint lint-cli lint-web lint-scripts \
        typecheck typecheck-cli typecheck-web \
        test test-cli test-web build-web \
        docker docker-web docker-cli versions release-dry

help: ## List the targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: install-cli install-web ## Install both toolchains

install-cli: ## uv sync (set UV_PYTHON to pick the interpreter)
	cd cli && uv sync --locked --extra dev

install-web: ## npm ci
	cd web && npm ci

lint: lint-cli lint-web lint-scripts ## Lint everything

lint-cli: ## ruff over cli/
	$(RUFF) check .

lint-web: ## eslint over web/
	cd web && npm run lint

lint-scripts: ## shellcheck over scripts/
	@command -v shellcheck >/dev/null || { \
	  echo "shellcheck not found — brew install shellcheck (CI always runs it)."; exit 1; }
	find scripts -name '*.sh' -exec shellcheck {} +

typecheck: typecheck-cli typecheck-web ## Type-check everything

typecheck-cli: ## mypy over cli/
	$(MYPY)

# tsconfig.spec.json only compiles spec-reachable files; this pass sees the app.
typecheck-web: ## tsc over the web app sources
	cd web && npx tsc -p tsconfig.app.json --noEmit

test: test-cli test-web ## Run both test suites

test-cli: ## pytest
	cd cli && $(PY) pytest

test-web: ## Karma, headless, with coverage
	cd web && npx ng test --watch=false --browsers=ChromeHeadless --code-coverage

build-web: ## Production build (enforces the bundle budgets)
	cd web && npx ng build --configuration production

docker: docker-web docker-cli ## Build both images

docker-web: ## Build the nginx image
	docker build -t translora-web:$(TAG) ./web

docker-cli: ## Build the CLI image
	docker build -t translora-cli:$(TAG) ./cli

versions: ## Check every version file agrees
	bash scripts/check-versions.sh

release-dry: ## Dry-run the changelog (LIMIT=5 tags by default)
	bash scripts/create-releases.sh --limit $(LIMIT)
