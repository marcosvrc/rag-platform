"""Caso de uso de recuperação (RAG-034, seção 10.3/11 do plano): busca
vetorial (RAG-030) + busca lexical (RAG-031), fundidas por RRF
(RAG-032) e reordenadas por um reranker configurável (RAG-033) —
"expor recuperação sem geração". Sem persistência: este caso de uso não
grava `QueryLog`/`QueryEvidence` (seção 9 do plano) — isso só acontece
no endpoint `query` (RAG-044, que integra recuperação + geração), que
tem um `query_id` para associar a cada evidência. Aqui a resposta só
existe no HTTP response.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from packages.application.errors import NotFoundError
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.lexical_search import LexicalSearchPort
from packages.application.ports.reranker import RerankerPort, rerank_safely
from packages.application.ports.vector_search import VectorSearchPort
from packages.domain.entities.chunk import Chunk
from packages.retrieval.rrf import reciprocal_rank_fusion

#: Quantos candidatos cada busca (vetorial/lexical) traz ANTES da
#: fusão/filtro/reranking — deliberadamente bem maior que `MAX_TOP_K`
#: (`packages/contracts/retrieval.py`) para que aplicar um filtro
#: (`page`/`section`) depois da fusão não esvazie o resultado final só
#: porque o pool de candidatos era pequeno demais. Uma constante de
#: código, mesma razão de não ser configurável de `DEFAULT_TOP_K`/
#: `MAX_TOP_K` (ver contrato).
CANDIDATE_POOL_SIZE = 100

#: Teto defensivo de `top_k`, reaplicado aqui mesmo já validado pelo
#: contrato (`RetrieveRequest.top_k`, `ge=1, le=MAX_TOP_K`) — mesma
#: postura de `packages/application/queries/knowledge_base.py::
#: list_knowledge_bases` (que reclampa `limit` mesmo já bounded pelo
#: `Query(...)` do router): este caso de uso nunca confia inteiramente
#: em validação de uma camada de fora para sua própria invariante.
MAX_TOP_K = 50


@dataclass(frozen=True, slots=True)
class RetrievalFilters:
    """Filtros permitidos sobre os chunks recuperados — o equivalente,
    na camada de aplicação, de `RetrievalFiltersRequest`
    (`packages/contracts/retrieval.py`). Um dataclass próprio em vez de
    reusar o contrato Pydantic: `packages/application` nunca importa
    `packages/contracts` (nem o inverso, ver essas duas pastas) —
    quem router traduz um para o outro, mesmo padrão de
    `KnowledgeBaseUpdateRequest.model_dump(...)` virando um `fields`
    dict em `apps/api/routers/knowledge_bases.py`."""

    page: int | None = None
    section: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    """Um chunk recuperado, com os scores e a posição no ranking
    final — o resultado deste caso de uso, antes de virar
    `RetrievedEvidenceResponse` no router."""

    chunk: Chunk
    retrieval_score: float
    rerank_score: float | None
    position: int


def _matches_filters(chunk: Chunk, filters: RetrievalFilters) -> bool:
    if filters.page is not None and chunk.page != filters.page:
        return False
    return not (filters.section is not None and chunk.section != filters.section)


async def retrieve_evidence(
    *,
    knowledge_base_repository: KnowledgeBaseRepositoryPort,
    embedding_provider: EmbeddingProviderPort,
    vector_search: VectorSearchPort,
    lexical_search: LexicalSearchPort,
    reranker: RerankerPort,
    reranker_enabled: bool,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    query: str,
    top_k: int,
    filters: RetrievalFilters | None = None,
) -> list[RetrievedEvidence]:
    """Recupera as `top_k` evidências mais relevantes para `query`, na
    base `knowledge_base_id` de `tenant_id`.

    Levanta `NotFoundError` se a base não existe (ou é de outro tenant —
    mesmo critério "404, nunca 403" de todo o resto da API, ver
    `packages/application/commands/document.py`). `filters`, quando
    informado, restringe o resultado aos chunks cujo `page`/`section`
    correspondam (`None` num campo do filtro não restringe por ele);
    aplicado DEPOIS da fusão RRF e ANTES do reranking, num pool de
    candidatos (`CANDIDATE_POOL_SIZE`) bem maior que `top_k` — a
    aplicação nunca filtra ao nível da query SQL de
    `VectorSearchPort`/`LexicalSearchPort` (RAG-030/031 não expõem
    filtro nenhum nessas portas); uma decisão de escopo desta atividade,
    documentada aqui: revisitar se o pool de candidatos se mostrar
    pequeno demais para filtros muito seletivos em bases grandes.

    `reranker_enabled` decide só se `rerank_score` no resultado reflete
    de verdade um score de reranking (`True`) ou é sempre `None`
    (`False`, reranking desativado — ver docstring de
    `RetrievedEvidenceResponse` sobre por que não expor o score
    reaproveitado do `PassthroughReranker` como se fosse um de
    verdade); `reranker` em si é sempre chamado via `rerank_safely`
    (RAG-033, "timeout usa ranking anterior") independente desse flag —
    quem decide QUAL adapter (`LiteLLMReranker` vs. `PassthroughReranker`)
    foi injetado em `reranker` é `apps/api/routers/retrieval.py`, não
    este caso de uso."""
    knowledge_base = await knowledge_base_repository.get_by_id(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
    )
    if knowledge_base is None:
        raise NotFoundError(detail="Base de conhecimento não encontrada.")

    bounded_top_k = max(1, min(top_k, MAX_TOP_K))

    [query_embedding] = await embedding_provider.embed(texts=[query])

    vector_results, lexical_results = await asyncio.gather(
        vector_search.search(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            query_embedding=query_embedding,
            limit=CANDIDATE_POOL_SIZE,
        ),
        lexical_search.search(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            query=query,
            limit=CANDIDATE_POOL_SIZE,
        ),
    )

    fused = reciprocal_rank_fusion(vector_results=vector_results, lexical_results=lexical_results)
    if filters is not None:
        fused = [scored for scored in fused if _matches_filters(scored.chunk, filters)]

    retrieval_score_by_chunk_id = {scored.chunk.id: scored.score for scored in fused}

    reranked = await rerank_safely(reranker, query=query, candidates=fused, top_n=bounded_top_k)

    return [
        RetrievedEvidence(
            chunk=scored.chunk,
            retrieval_score=retrieval_score_by_chunk_id[scored.chunk.id],
            rerank_score=scored.score if reranker_enabled else None,
            position=position,
        )
        for position, scored in enumerate(reranked)
    ]
