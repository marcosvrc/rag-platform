# rag-platform — atalhos de desenvolvimento local
#
# Requer Python 3.12 disponível como `python3.12` no PATH (ou defina PYTHON=...).
PYTHON ?= python3.12
VENV := .venv
BIN := $(VENV)/bin

.PHONY: venv install lint format test clean

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	$(BIN)/pip install -e ".[dev]"

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format:
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .

test:
	$(BIN)/pytest

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache **/__pycache__
