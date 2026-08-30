"""Contratos HTTP do endpoint `/v1/query` (RAG-044, seção 10.3/10.5 do
plano: "expor recuperação com geração").

`QueryRequest` reaproveita `RetrievalFiltersRequest`
(`packages/contracts/retrieval.py`) — os mesmos filtros permitidos
(`page`/`section`), mesma justificativa de "bloqueia filtro arbitrário"
via `extra='forbid'`; não faz sentido duplicar esse contrato só porque
este é um endpoint diferente.

`QueryResponse` segue exatamente o formato da seção 10.5 do plano:
`query_id`, `answer`, `grounded`, `citations`, `model`, `usage`
(`input_tokens`/`output_tokens` — o mesmo objeto de valor `TokenUsage`
de `QueryLog`, RAG-010, só como contrato HTTP) e `trace_id`. Sem
`knowledge_base_id`/`query` ecoados de volta (ao contrário de
`RetrieveResponse`) — o exemplo da seção 10.5 não os inclui."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.retrieval import MAX_TOP_K, RetrievalFiltersRequest

#: Mesmos defaults de `RetrieveRequest` (`packages/contracts/
#: retrieval.py`) — `top_k` aqui tem o mesmo significado (quantas
#: evidências a recuperação traz antes de montar o contexto), reusar o
#: mesmo teto evita dois números "máximo de resultados" divergentes
#: para o mesmo conceito.
DEFAULT_TOP_K = 10


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    filters: RetrievalFiltersRequest | None = None


class CitationResponse(BaseModel):
    """Uma citação da resposta (seção 10.5 do plano)."""

    document_id: UUID
    document_name: str
    chunk_id: UUID
    page: int | None
    section: str | None
    excerpt: str
    score: float


class TokenUsageResponse(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class QueryResponse(BaseModel):
    query_id: UUID
    answer: str
    grounded: bool
    citations: list[CitationResponse]
    model: str
    usage: TokenUsageResponse
    trace_id: UUID
