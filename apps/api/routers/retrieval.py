"""Endpoint de recuperação (RAG-034, seção 10.3 do plano): expõe busca
vetorial + lexical + fusão RRF + reranking configurável, sem geração
nem persistência (ver `packages/application/queries/retrieval.py`).

Mesmo isolamento por tenant de `apps/api/routers/knowledge_bases.py`/
`documents.py` (RAG-012/RAG-051): uma base de outro tenant (ou
inexistente) retorna 404, nunca 403.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.lexical_search.postgres import PostgresLexicalSearch
from adapters.litellm.embedding_provider import LiteLLMEmbeddingProvider
from adapters.postgres.engine import get_session
from adapters.reranker.litellm import LiteLLMReranker
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.postgres import PostgresVectorSearch
from apps.api.dependencies import get_current_tenant_id, get_settings_dependency
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.lexical_search import LexicalSearchPort
from packages.application.ports.reranker import RerankerPort
from packages.application.ports.vector_search import VectorSearchPort
from packages.application.queries import retrieval as retrieval_queries
from packages.config.settings import Settings
from packages.contracts.retrieval import (
    RetrievedEvidenceResponse,
    RetrieveRequest,
    RetrieveResponse,
)

router = APIRouter(prefix="/v1/knowledge-bases", tags=["retrieval"])


async def get_vector_search(session: AsyncSession = Depends(get_session)) -> VectorSearchPort:
    """`Depends()` próprio, mesmo padrão de `get_knowledge_base_repository`
    — os testes sobrescrevem via `app.dependency_overrides`."""
    return PostgresVectorSearch(session)


async def get_lexical_search(session: AsyncSession = Depends(get_session)) -> LexicalSearchPort:
    return PostgresLexicalSearch(session)


async def get_embedding_provider(
    settings: Settings = Depends(get_settings_dependency),
) -> EmbeddingProviderPort:
    return LiteLLMEmbeddingProvider(settings)


async def get_reranker(settings: Settings = Depends(get_settings_dependency)) -> RerankerPort:
    """ "pode ser desativado" (critério de aceite da RAG-033): este é o
    ponto de injeção prometido pela docstring de
    `packages/application/ports/reranker.py` — qual `RerankerPort` é
    injetado depende só de `Settings.reranker_enabled`, nunca de um
    `if` dentro de um adapter só."""
    if settings.reranker_enabled:
        return LiteLLMReranker(settings)
    return PassthroughReranker()


def _to_response(
    knowledge_base_id: UUID, query: str, evidence: list[retrieval_queries.RetrievedEvidence]
) -> RetrieveResponse:
    return RetrieveResponse(
        knowledge_base_id=knowledge_base_id,
        query=query,
        evidence=[
            RetrievedEvidenceResponse(
                chunk_id=item.chunk.id,
                knowledge_base_id=item.chunk.knowledge_base_id,
                content=item.chunk.content,
                page=item.chunk.page,
                section=item.chunk.section,
                metadata=item.chunk.metadata,
                retrieval_score=item.retrieval_score,
                rerank_score=item.rerank_score,
                position=item.position,
            )
            for item in evidence
        ],
    )


@router.post("/{knowledge_base_id}/retrieve", response_model=RetrieveResponse)
async def retrieve(
    knowledge_base_id: UUID,
    payload: RetrieveRequest,
    tenant_id: UUID = Depends(get_current_tenant_id),
    settings: Settings = Depends(get_settings_dependency),
    knowledge_base_repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
    embedding_provider: EmbeddingProviderPort = Depends(get_embedding_provider),
    vector_search: VectorSearchPort = Depends(get_vector_search),
    lexical_search: LexicalSearchPort = Depends(get_lexical_search),
    reranker: RerankerPort = Depends(get_reranker),
) -> RetrieveResponse:
    filters = (
        retrieval_queries.RetrievalFilters(
            page=payload.filters.page, section=payload.filters.section
        )
        if payload.filters is not None
        else None
    )
    evidence = await retrieval_queries.retrieve_evidence(
        knowledge_base_repository=knowledge_base_repository,
        embedding_provider=embedding_provider,
        vector_search=vector_search,
        lexical_search=lexical_search,
        reranker=reranker,
        reranker_enabled=settings.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query=payload.query,
        top_k=payload.top_k,
        filters=filters,
    )
    return _to_response(knowledge_base_id, payload.query, evidence)
