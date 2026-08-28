# rag-platform
Self-service RAG platform for document ingestion, hybrid retrieval, grounded generation, evaluation, governance, and observability.

## Status

Este repositório está sendo construído a partir de
`rag-platform-llm-implementation-plan.md`, seguindo o backlog de atividades
(`RAG-XXX`) descrito nesse documento. Cada atividade é entregue em uma
branch/PR separada.

Concluído até o momento:

- **RAG-001 — Inicializar o repositório** (estrutura de diretórios e
  configuração Python de base).
- **RAG-002 — Padronizar qualidade de código** (Ruff, Mypy, Pytest e
  cobertura).

## Desenvolvimento local

Pré-requisitos: Python 3.12.

```bash
make install    # cria .venv e instala dependências de desenvolvimento
make lint       # ruff check + ruff format --check
make typecheck  # mypy sobre apps/, packages/, adapters/ e tests/
make test       # pytest com relatório de cobertura (term-missing + XML)
make check      # lint + typecheck + test — o "pipeline" local completo
```

### Cobertura de testes

`make test` sempre gera relatório de cobertura (`--cov-report=term-missing`
e `coverage.xml`), mas o gate numérico (`fail_under`, ver
`[tool.coverage.report]` em `pyproject.toml`) está deliberadamente em `0`
por enquanto: `apps/`, `packages/` e `adapters/` ainda contêm apenas
módulos vazios (0 statements), então qualquer percentual fixo seria
artificial. O gate será elevado (baseline planejada: 85%) a partir da
primeira atividade que introduzir código de aplicação real (RAG-010 em
diante).

A configuração completa de ambiente de execução (variáveis, serviços de
infraestrutura via Docker Compose, banco de dados etc.) é adicionada nas
próximas atividades do backlog (RAG-003 em diante).
