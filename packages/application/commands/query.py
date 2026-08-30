"""Caso de uso do endpoint `/v1/query` (RAG-044, seção 10.3/12 do
plano): integra recuperação (RAG-034), montagem de contexto (RAG-041),
geração (RAG-042), validação de groundedness (RAG-043) e persistência
(`QueryLog`/`QueryEvidence`, RAG-010) — "expor recuperação COM geração",
o par de `packages/application/queries/retrieval.py`.

Segue os passos 9-14 da seção 12 do plano (os passos 1-8 já são
inteiramente responsabilidade de `retrieve_evidence`, reaproveitado aqui
sem duplicação):

  9. Aplicar limiar mínimo.
  10. Montar contexto dentro do orçamento de tokens.
  11. Chamar modelo por alias no LiteLLM.
  12. Validar formato e citações.
  13. Persistir log e evidências.
  14. Retornar resposta.

`generation_model_alias`/`generation_fallback_alias` chegam como
parâmetros explícitos (resolvidos por quem chama, o router — RAG-042 a
partir de `packages.config.models`) em vez deste módulo importar
`packages.config.models` diretamente: nenhum outro caso de uso em
`packages/application` faz essa importação (só adapters resolvem alias
de modelo, ver `adapters/litellm/generation_provider.py`) — mesma
disciplina de decoupling da seção 5.1 do plano, aplicada aqui à
configuração, não só à infraestrutura de terceiros.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from packages.application.errors import ServiceUnavailableError
from packages.application.ports.document_repository import DocumentRepositoryPort
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.generation_provider import (
    GenerationError,
    GenerationProviderPort,
)
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.lexical_search import LexicalSearchPort
from packages.application.ports.query_repository import QueryEvidenceInput, QueryRepositoryPort
from packages.application.ports.reranker import RerankerPort
from packages.application.ports.vector_search import VectorSearchPort
from packages.application.queries import retrieval as retrieval_queries
from packages.application.queries.retrieval import RetrievalFilters, RetrievedEvidence
from packages.domain.entities.query_log import TokenUsage
from packages.generation.context_builder import ContextBuildResult, build_context
from packages.generation.groundedness import enforce_groundedness
from packages.generation.prompts import PromptTemplate

#: RAG-044, seção 12, passo 9: nenhum modelo é chamado quando a
#: evidência recuperada não passa no limiar (`Settings.
#: retrieval_minimum_score`) ou quando nada coube no orçamento de
#: contexto (RAG-041). Este rótulo torna essa decisão visível em
#: `QueryLog.model`, em vez de registrar um alias de modelo que na
#: verdade nunca foi invocado (o que sugeriria, incorretamente, que uma
#: chamada de geração aconteceu).
NO_GENERATION_MODEL_LABEL = "sem-geracao-evidencia-insuficiente"

#: Tamanho máximo do trecho (`excerpt`) de uma citação na resposta
#: (seção 10.5 do plano) — um chunk inteiro pode ser bem maior que o
#: necessário para o usuário reconhecer de onde veio a afirmação
#: citada; corta em um limite de caracteres (nunca no meio de uma
#: palavra) com reticências, só para exibição — nunca afeta o texto que
#: o modelo recebeu como contexto (RAG-041) nem a validação de
#: groundedness (RAG-043), que sempre usam o conteúdo completo do
#: chunk.
EXCERPT_MAX_CHARS = 300


@dataclass(frozen=True, slots=True)
class Citation:
    """Uma citação da resposta, com o suficiente para o cliente
    localizar a fonte (seção 10.5 do plano: "citations")."""

    document_id: UUID
    document_name: str
    chunk_id: UUID
    page: int | None
    section: str | None
    excerpt: str
    score: float


@dataclass(frozen=True, slots=True)
class QueryAnswer:
    """Resultado completo do caso de uso — o suficiente para o router
    montar a resposta HTTP (seção 10.5 do plano) sem decidir nada."""

    query_id: UUID
    answer: str
    grounded: bool
    citations: tuple[Citation, ...]
    model: str
    token_usage: TokenUsage
    trace_id: UUID


def _effective_score(item: RetrievedEvidence) -> float:
    """O score que de fato decidiu a posição final deste item no
    ranking: de rerank quando reranking rodou de verdade, senão o de
    retrieval (RRF) — mesmo critério usado para o `score` exposto em
    cada citação."""
    return item.rerank_score if item.rerank_score is not None else item.retrieval_score


def _has_sufficient_evidence(
    evidence: Sequence[RetrievedEvidence], *, minimum_score: float
) -> bool:
    """Passo 9 (seção 12.1 do plano): há evidência suficiente quando
    pelo menos um item ultrapassa `minimum_score`. `evidence` vazia
    nunca satisfaz isso (`any` sobre uma sequência vazia é `False`) —
    não é um caso especial, é a mesma regra."""
    return any(_effective_score(item) >= minimum_score for item in evidence)


def _build_excerpt(content: str) -> str:
    stripped = content.strip()
    if len(stripped) <= EXCERPT_MAX_CHARS:
        return stripped
    truncated = stripped[:EXCERPT_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{truncated}…"


def _to_evidence_inputs(evidence: Sequence[RetrievedEvidence]) -> list[QueryEvidenceInput]:
    return [
        QueryEvidenceInput(
            chunk_id=item.chunk.id,
            retrieval_score=item.retrieval_score,
            rerank_score=item.rerank_score,
            position=item.position,
        )
        for item in evidence
    ]


async def _persist_no_generation(
    *,
    query_repository: QueryRepositoryPort,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    query: str,
    evidence: Sequence[RetrievedEvidence],
    latency_ms: int,
    trace_id: UUID,
    no_evidence_response: str,
) -> QueryAnswer:
    query_log = await query_repository.persist_query(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        question_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        model=NO_GENERATION_MODEL_LABEL,
        latency_ms=latency_ms,
        token_usage=TokenUsage(input_tokens=0, output_tokens=0),
        trace_id=trace_id,
        evidence=_to_evidence_inputs(evidence),
    )
    return QueryAnswer(
        query_id=query_log.id,
        answer=no_evidence_response,
        grounded=False,
        citations=(),
        model=NO_GENERATION_MODEL_LABEL,
        token_usage=query_log.token_usage,
        trace_id=trace_id,
    )


async def _build_citations(
    *,
    document_repository: DocumentRepositoryPort,
    context_result: ContextBuildResult,
    cited_chunk_ids: frozenset[UUID],
) -> tuple[Citation, ...]:
    cited_evidence = [
        item for item in context_result.included_evidence if item.chunk.id in cited_chunk_ids
    ]
    documents_by_chunk_id = await document_repository.get_documents_by_chunk_ids(
        chunk_ids=[item.chunk.id for item in cited_evidence]
    )
    return tuple(
        Citation(
            document_id=document.id,
            document_name=document.name,
            chunk_id=item.chunk.id,
            page=item.chunk.page,
            section=item.chunk.section,
            excerpt=_build_excerpt(item.chunk.content),
            score=_effective_score(item),
        )
        for item in cited_evidence
        if (document := documents_by_chunk_id.get(item.chunk.id)) is not None
    )


async def answer_query(
    *,
    knowledge_base_repository: KnowledgeBaseRepositoryPort,
    document_repository: DocumentRepositoryPort,
    query_repository: QueryRepositoryPort,
    embedding_provider: EmbeddingProviderPort,
    vector_search: VectorSearchPort,
    lexical_search: LexicalSearchPort,
    reranker: RerankerPort,
    reranker_enabled: bool,
    generation_provider: GenerationProviderPort,
    generation_model_alias: str,
    generation_fallback_alias: str | None,
    prompt_template: PromptTemplate,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    query: str,
    top_k: int,
    filters: RetrievalFilters | None,
    retrieval_minimum_score: float,
    context_token_budget: int,
    trace_id: UUID,
) -> QueryAnswer:
    """Executa os passos 9-14 do fluxo de consulta (seção 12 do plano).

    Levanta `NotFoundError` (propagada de `retrieve_evidence`) se a base
    não existe ou é de outro tenant. Levanta `ServiceUnavailableError`
    se o gateway de geração falhar depois de esgotar suas tentativas
    (RAG-042) — uma indisponibilidade real de infraestrutura, nunca
    tratada como "resposta sem suporte" (RAG-043, que só se aplica a
    UMA resposta que o modelo de fato gerou)."""
    started_at = time.monotonic()

    evidence = await retrieval_queries.retrieve_evidence(
        knowledge_base_repository=knowledge_base_repository,
        embedding_provider=embedding_provider,
        vector_search=vector_search,
        lexical_search=lexical_search,
        reranker=reranker,
        reranker_enabled=reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query=query,
        top_k=top_k,
        filters=filters,
    )

    context_result = (
        build_context(evidence, token_budget=context_token_budget)
        if _has_sufficient_evidence(evidence, minimum_score=retrieval_minimum_score)
        else None
    )

    # Mesmo quando a evidência passa no limiar por score individual, o
    # contexto pode sair vazio (RAG-041: nada coube no orçamento) —
    # mesmo desfecho seguro de "evidência insuficiente", sem gastar uma
    # chamada real de geração.
    if context_result is None or not context_result.context_text:
        return await _persist_no_generation(
            query_repository=query_repository,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            query=query,
            evidence=evidence,
            latency_ms=round((time.monotonic() - started_at) * 1000),
            trace_id=trace_id,
            no_evidence_response=prompt_template.no_evidence_response,
        )

    prompt = prompt_template.render(context=context_result.context_text, question=query)
    try:
        generation_result = await generation_provider.generate(prompt=prompt)
    except GenerationError as exc:
        raise ServiceUnavailableError(
            detail="O serviço de geração de respostas está indisponível no momento."
        ) from exc

    outcome = enforce_groundedness(
        generation_result.content,
        included_evidence=context_result.included_evidence,
        no_evidence_response=prompt_template.no_evidence_response,
    )
    grounded = not outcome.fallback_applied and bool(outcome.cited_chunk_ids)

    citations: tuple[Citation, ...] = ()
    if grounded:
        citations = await _build_citations(
            document_repository=document_repository,
            context_result=context_result,
            cited_chunk_ids=outcome.cited_chunk_ids,
        )

    if generation_result.used_fallback:
        assert generation_fallback_alias is not None
        model_used = generation_fallback_alias
    else:
        model_used = generation_model_alias

    query_log = await query_repository.persist_query(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        question_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        model=model_used,
        latency_ms=round((time.monotonic() - started_at) * 1000),
        token_usage=TokenUsage(
            input_tokens=generation_result.prompt_tokens,
            output_tokens=generation_result.completion_tokens,
        ),
        trace_id=trace_id,
        evidence=_to_evidence_inputs(evidence),
    )

    return QueryAnswer(
        query_id=query_log.id,
        answer=outcome.content,
        grounded=grounded,
        citations=citations,
        model=query_log.model,
        token_usage=query_log.token_usage,
        trace_id=trace_id,
    )
