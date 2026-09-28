.DEFAULT_GOAL := help
# Recipes must stay POSIX sh: GNU make for Windows cannot resolve /bin/bash and
# runs them with the sh.exe that Git Bash puts on PATH instead.
SHELL := /bin/bash
DS ?= opssat

# Without any sh.exe on PATH, make for Windows falls back to cmd.exe.
ifeq ($(SHELL),sh.exe)
$(error no POSIX shell on PATH: on Windows, run make from Git Bash)
endif

# --- Python environment ---------------------------------------------------------
# uv is used when it is on PATH: it installs exactly what uv.lock pins, on the
# interpreter pinned in .python-version. Without uv, the targets fall back to a
# plain .venv managed by pip, which does not read uv.lock.
# Force the fallback with: make <target> UV=
UV := $(if $(shell command -v uv 2>/dev/null),uv)

# Fallback only: the first Python >= 3.12 on PATH. The Windows Store "python"
# alias fails the probe and is skipped. Override with PYTHON=/path/to/python.
PYTHON ?= $(shell for p in python3.12 "py -3.12" python3 python "py -3"; do \
	$$p -c 'import sys; sys.exit(sys.version_info < (3, 12))' >/dev/null 2>&1 \
	&& { echo "$$p"; break; }; done)

ifeq ($(OS),Windows_NT)
VENV_PY := .venv/Scripts/python.exe
else
VENV_PY := .venv/bin/python
endif

ifneq ($(UV),)
PY := $(UV) run --all-extras python
ENV :=
else
PY := $(VENV_PY)
ENV := .venv/.pip-installed
endif

.PHONY: help setup lint fmt typecheck test check data-list data-fetch replay train evaluate dev-up dev-down

help: ## Show the available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: $(ENV) ## Create .venv and install dependencies (uv, or pip without it)
ifneq ($(UV),)
	$(UV) sync --all-extras
endif

.venv/.pip-installed: pyproject.toml
	@test -n '$(PYTHON)' || { echo "no Python >= 3.12 on PATH: install uv, or pass PYTHON=/path/to/python3.12" >&2; exit 1; }
	@echo "uv not on PATH: setting up .venv with pip"
	$(PYTHON) -m venv .venv
	$(VENV_PY) -m pip install -e ".[dev]"
	@touch $@

lint: $(ENV) ## Formatting and lint checks
	$(PY) -m ruff format --check .
	$(PY) -m ruff check .

fmt: $(ENV) ## Apply formatting and safe fixes
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .

typecheck: $(ENV) ## Static type checking
	$(PY) -m mypy

test: $(ENV) ## Unit tests
	$(PY) -m pytest

check: lint typecheck test ## Everything CI runs

data-list: $(ENV) ## Show the dataset registry
	$(PY) scripts/fetch_data.py --list

data-fetch: $(ENV) ## Download a dataset: make data-fetch DS=opssat
	$(PY) scripts/fetch_data.py $(DS)

# --- not implemented yet: see the roadmap in README.md ---

replay: ## [M1] Replay a mission window as a live stream
	@echo "not implemented yet - milestone M1"; exit 1

train: ## [M5] Train a detector from a config file
	@echo "not implemented yet - milestone M5"; exit 1

evaluate: ## [M4] Reproducible benchmark run
	@echo "not implemented yet - milestone M4"; exit 1

dev-up: ## [M2] Local stack: k3s, Redpanda, TimescaleDB, MinIO
	@echo "not implemented yet - milestone M2"; exit 1

dev-down: ## [M2] Tear the local stack down
	@echo "not implemented yet - milestone M2"; exit 1
