"""Ponto de entrada da API FastAPI (RAG-005).

Endpoints de bases de conhecimento chegam em RAG-012; upload de
documentos, em RAG-021; recuperação (sem geração), em RAG-034; demais
endpoints de negócio (`/v1/...`), em atividades seguintes.
"""

from fastapi import FastAPI

from apps.api.errors import register_error_handlers
from apps.api.routers import documents, health, jobs, knowledge_bases, retrieval
from packages.observability.metrics import configure_metrics
from packages.observability.tracing import configure_tracing, instrument_fastapi_app


def create_app() -> FastAPI:
    """Application factory: monta a instância do FastAPI, seus routers e
    o tratamento padronizado de erros (RAG-013)."""
    app = FastAPI(
        title="rag-platform API",
        version="0.1.0",
        description=("Plataforma RAG multi-tenant — ver rag-platform-llm-implementation-plan.md."),
    )
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(knowledge_bases.router)
    app.include_router(documents.router)
    app.include_router(jobs.router)
    app.include_router(retrieval.router)
    # RAG-052 — seguro chamar aqui (sem Settings, ver
    # packages/observability/tracing.py): não quebra os testes que
    # importam esta app sem nenhuma variável de ambiente de negócio
    # configurada.
    configure_tracing(service_name="rag-platform-api")
    configure_metrics(service_name="rag-platform-api")
    instrument_fastapi_app(app)
    return app


app = create_app()
