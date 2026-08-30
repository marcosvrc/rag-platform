"""Contratos HTTP do endpoint de recuperação (RAG-034, seção 10.3 do
plano: "expor recuperação sem geração").

Separado da entidade de domínio `QueryEvidence` de propósito (mesmo
racional de `packages/contracts/knowledge_base.py`): `QueryEvidence`
(seção 9 do plano) exige `query_id` — só existe depois que uma consulta
é persistida, o que só acontece no endpoint `query` (RAG-044, que
integra retrieval + geração). Este endpoint é só recuperação, sem
geração nem persistência — não há `query_id` para carregar, então a
resposta usa um contrato próprio (`RetrievedEvidenceResponse`), nunca
`QueryEvidence` diretamente.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Quantos resultados o endpoint devolve por padrão, e o teto que
#: `top_k` pode pedir. Mesma convenção de paginação de
#: `packages/application/queries/knowledge_base.py`
#: (`DEFAULT_PAGE_SIZE`/`MAX_PAGE_SIZE`): uma constante de código, não
#: uma variável de ambiente — não há razão de produto para tornar isso
#: configurável por deployment ainda.
DEFAULT_TOP_K = 10
MAX_TOP_K = 50


class RetrievalFiltersRequest(BaseModel):
    """Filtros permitidos sobre os chunks recuperados (critério de
    aceite "suporta filtros permitidos; bloqueia filtros arbitrários").

    `extra="forbid"` (mesma convenção de
    `KnowledgeBaseCreateRequest`/`KnowledgeBaseUpdateRequest`) é o que
    bloqueia um filtro arbitrário: qualquer chave fora de `page`/
    `section` vira 422 automaticamente, na validação do Pydantic —
    nenhuma lista de bloqueio a mais para manter. Os dois campos
    permitidos correspondem aos únicos campos estruturados e tipados de
    `Chunk` (RAG-024) fora de `metadata` (um `dict[str, Any]` livre,
    sem chave estável entre documentos — filtrar por uma chave
    arbitrária dentro dele é exatamente o que este contrato bloqueia).
    """

    model_config = ConfigDict(extra="forbid")

    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, min_length=1)


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=DEFAULT_TOP_K, ge=1, le=MAX_TOP_K)
    filters: RetrievalFiltersRequest | None = None


class RetrievedEvidenceResponse(BaseModel):
    """Uma evidência (chunk recuperado), com os scores usados para
    chegar à posição final (critério de aceite "retorna evidências,
    metadados e scores").

    `rerank_score` é `None` quando reranking está desativado
    (`Settings.reranker_enabled=False`, `PassthroughReranker`) — o
    valor nesse caso seria só o `retrieval_score` reaproveitado
    verbatim (ver `adapters/reranker/passthrough.py`), então expô-lo
    como "score de reranking" seria enganoso; `null` é honesto sobre
    reranking não ter acontecido de verdade, incluindo no caso raro em
    que ele foi tentado mas caiu no fallback de `rerank_safely` (RAG-033,
    "timeout usa ranking anterior") — o valor reaproveitado do ranking
    anterior não é um score de reranking real nesse caso também."""

    chunk_id: UUID
    knowledge_base_id: UUID
    content: str
    page: int | None
    section: str | None
    metadata: dict[str, Any]
    retrieval_score: float
    rerank_score: float | None
    position: int


class RetrieveResponse(BaseModel):
    knowledge_base_id: UUID
    query: str
    evidence: list[RetrievedEvidenceResponse]
