# rag-platform — atalhos de desenvolvimento local
#
# Requer Python 3.12 disponível como `python3.12` no PATH (ou defina PYTHON=...).
PYTHON ?= python3.12
VENV := .venv
BIN := $(VENV)/bin

.PHONY: venv install lint format typecheck test security run-api run-worker check rag-quality-gate clean

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

# RAG-052: variáveis lidas via os.getenv puro (OTEL_*, ver
# packages/observability/tracing.py) não vêm do parsing interno do
# Pydantic Settings — só ficam visíveis ao processo se estiverem no
# ambiente real do shell. "set -a; . ./.env; set +a" (só quando o
# arquivo existe) exporta todo `.env` para variáveis de ambiente reais
# antes de subir o processo, sem mudar como Settings lê `.env` (RAG-004
# continua igual).
run-api:
	set -a; [ -f .env ] && . ./.env; set +a; $(BIN)/uvicorn apps.api.main:app --reload --port 8000

run-worker:
	set -a; [ -f .env ] && . ./.env; set +a; $(BIN)/celery -A apps.indexing_worker.worker worker --loglevel=info

# "Pipeline" local completo: o mesmo conjunto de verificações que o CI
# (RAG-070+) deve reproduzir. Falha se lint, tipos, testes ou cobertura
# mínima (85%, ver [tool.coverage.report] em pyproject.toml) não passarem.
check: lint typecheck test security

# RAG-073 — reproduz localmente o quality gate de RAG: avaliação
# reduzida (Recall@K/MRR + faithfulness/answer relevancy sobre os 3
# primeiros casos respondíveis do dataset dourado) e verificação de
# regressão contra a baseline aprovada (RAG-063). Ao contrário de
# `make check`, exige um gateway LiteLLM alcançável (LITELLM_BASE_URL)
# — chama modelos de verdade (seção 15 do plano), então nunca faz
# parte de `check`/CI comum (RAG-070); só do workflow dedicado
# `.github/workflows/rag-quality-gate.yml`.
rag-quality-gate:
	$(BIN)/python scripts/run_retrieval_evaluation.py --max-cases 3
	$(BIN)/python scripts/run_generation_evaluation.py --max-cases 3
	$(BIN)/python scripts/check_evaluation_baseline.py \
		--report reports/retrieval-evaluation/retrieval-evaluation.json \
		--report reports/generation-evaluation/generation-evaluation.json

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache .mypy_cache .coverage coverage.xml **/__pycache__
