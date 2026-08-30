"""Endpoint de consulta com geração (RAG-044, seção 10.3 do plano):
integra recuperação (RAG-034), contexto (RAG-041), geração (RAG-042) e
validação de groundedness (RAG-043), persistindo `QueryLog`/
`QueryEvidence` (RAG-010).

Mesmo isolamento por tenant de `apps/api/routers/retrieval.py`: uma base
de outro tenant (ou inexistente) retorna 404, nunca 403 — propagado de
`retrieve_evidence` via `NotFoundError`."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.litellm.generation_provider import LiteLLMGenerationProvider
from adapters.postgres.engine import get_session
from adapters.query_repository.postgres import PostgresQueryRepository
from apps.api.dependencies import get_current_tenant_id, get_settings_dependency
from apps.api.routers.documents import get_document_repository
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from apps.api.routers.retrieval import (
    get_embedding_provider,
    get_lexical_search,
    get_reranker,
    get_vector_search,
)
from packages.application.commands import query as query_commands
from packages.application.ports.document_repository import DocumentRepositoryPort
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.generation_provider import GenerationProviderPort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.lexical_search import LexicalSearchPort
from packages.application.ports.query_repository import QueryRepositoryPort
from packages.application.ports.reranker import RerankerPort
from packages.application.ports.vector_search import VectorSearchPort
from packages.application.queries import retrieval as retrieval_queries
from packages.config.models import (
    get_default_generation_fallback_model,
    get_default_generation_model,
)
from packages.config.settings import Settings
from packages.contracts.query import (
    CitationResponse,
    QueryRequest,
    QueryResponse,
    TokenUsageResponse,
)
from packages.generation.prompts import get_default_answer_prompt
from packages.observability.tracing import get_current_trace_id

router = APIRouter(prefix="/v1/knowledge-bases", tags=["query"])


async def get_query_repository(session: AsyncSession = Depends(get_session)) -> QueryRepositoryPort:
    """`Depends()` próprio, mesmo padrão de `get_document_repository` —
    os testes sobrescrevem via `app.dependency_overrides`."""
    return PostgresQueryRepository(session)


async def get_generation_provider(
    settings: Settings = Depends(get_settings_dependency),
) -> GenerationProviderPort:
    """`Depends()` próprio, mesmo padrão de `get_embedding_provider`
    (`apps/api/routers/retrieval.py`) — os testes sobrescrevem via
    `app.dependency_overrides`."""
    return LiteLLMGenerationProvider(settings)


def _to_response(answer: query_commands.QueryAnswer) -> QueryResponse:
    return QueryResponse(
        query_id=answer.query_id,
        answer=answer.answer,
        grounded=answer.grounded,
        citations=[
            CitationResponse(
                document_id=citation.document_id,
                document_name=citation.document_name,
                chunk_id=citation.chunk_id,
                page=citation.page,
                section=citation.section,
                excerpt=citation.excerpt,
                score=citation.score,
            )
            for citation in answer.citations
        ],
        model=answer.model,
        usage=TokenUsageResponse(
            input_tokens=answer.token_usage.input_tokens,
            output_tokens=answer.token_usage.output_tokens,
        ),
        trace_id=answer.trace_id,
    )


@router.post("/{knowledge_base_id}/query", response_model=QueryResponse)
async def query(
    knowledge_base_id: UUID,
    payload: QueryRequest,
    tenant_id: UUID = Depends(get_current_tenant_id),
    settings: Settings = Depends(get_settings_dependency),
    knowledge_base_repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
    document_repository: DocumentRepositoryPort = Depends(get_document_repository),
    query_repository: QueryRepositoryPort = Depends(get_query_repository),
    embedding_provider: EmbeddingProviderPort = Depends(get_embedding_provider),
    vector_search: VectorSearchPort = Depends(get_vector_search),
    lexical_search: LexicalSearchPort = Depends(get_lexical_search),
    reranker: RerankerPort = Depends(get_reranker),
    generation_provider: GenerationProviderPort = Depends(get_generation_provider),
) -> QueryResponse:
    filters = (
        retrieval_queries.RetrievalFilters(
            page=payload.filters.page, section=payload.filters.section
        )
        if payload.filters is not None
        else None
    )
    generation_model_alias = get_default_generation_model().alias
    generation_fallback_alias = (
        get_default_generation_fallback_model().alias
        if settings.generation_fallback_enabled
        else None
    )
    answer = await query_commands.answer_query(
        knowledge_base_repository=knowledge_base_repository,
        document_repository=document_repository,
        query_repository=query_repository,
        embedding_provider=embedding_provider,
        vector_search=vector_search,
        lexical_search=lexical_search,
        reranker=reranker,
        reranker_enabled=settings.reranker_enabled,
        generation_provider=generation_provider,
        generation_model_alias=generation_model_alias,
        generation_fallback_alias=generation_fallback_alias,
        prompt_template=get_default_answer_prompt(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query=payload.query,
        top_k=payload.top_k,
        filters=filters,
        retrieval_minimum_score=settings.retrieval_minimum_score,
        context_token_budget=settings.generation_context_token_budget,
        trace_id=get_current_trace_id(),
    )
    return _to_response(answer)
