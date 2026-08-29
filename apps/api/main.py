"""Ponto de entrada da API FastAPI (RAG-005).

Endpoints de negócio (`/v1/...`) chegam a partir de RAG-012.
"""

from fastapi import FastAPI

from apps.api.routers import health


def create_app() -> FastAPI:
    """Application factory: monta a instância do FastAPI e seus routers."""
    app = FastAPI(
        title="rag-platform API",
        version="0.1.0",
        description=("Plataforma RAG multi-tenant — ver rag-platform-llm-implementation-plan.md."),
    )
    app.include_router(health.router)
    return app


app = create_app()
