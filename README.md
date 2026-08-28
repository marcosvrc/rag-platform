# rag-platform
Self-service RAG platform for document ingestion, hybrid retrieval, grounded generation, evaluation, governance, and observability.

## Status

Este repositório está sendo construído a partir de
`rag-platform-llm-implementation-plan.md`, seguindo o backlog de atividades
(`RAG-XXX`) descrito nesse documento. Cada atividade é entregue em uma
branch/PR separada.

Concluído até o momento: **RAG-001 — Inicializar o repositório** (estrutura
de diretórios e configuração Python de base).

## Desenvolvimento local (RAG-001)

Pré-requisitos: Python 3.12.

```bash
make install   # cria .venv e instala dependências de desenvolvimento
make lint      # ruff check + ruff format --check
make test      # pytest
```

A configuração completa de ambiente (variáveis, serviços de infraestrutura
via Docker Compose, banco de dados etc.) é adicionada nas próximas
atividades do backlog (RAG-002 em diante).
