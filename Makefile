# rag-platform — atalhos de desenvolvimento local
#
# Requer Python 3.12 disponível como `python3.12` no PATH (ou defina PYTHON=...).
PYTHON ?= python3.12
VENV := .venv
BIN := $(VENV)/bin

.PHONY: venv install lint format typecheck test security run-api check clean

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

typecheck:
	$(BIN)/mypy apps packages adapters tests

test:
	$(BIN)/pytest

# SAST (bandit) + SCA (pip-audit) + validação do arquivo de exceções
# (RAG-071). Não inclui secret scanning (gitleaks) nem lint de
# Dockerfile (hadolint): são binários externos, não pacotes Python —
# rodam só no workflow de PR (.github/workflows/security.yml).
security:
	$(BIN)/bandit -c pyproject.toml -r apps packages adapters --severity-level high
	$(BIN)/pip-audit
	$(BIN)/python scripts/check_security_exceptions.py

run-api:
	$(BIN)/uvicorn apps.api.main:app --reload --port 8000

# "Pipeline" local completo: o mesmo conjunto de verificações que o CI
# (RAG-070+) deve reproduzir. Falha se lint, tipos, testes ou cobertura
# mínima (85%, ver [tool.coverage.report] em pyproject.toml) não passarem.
check: lint typecheck test security

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml **/__pycache__
