"""Porta de persistência de consultas (RAG-044, seção 12 do plano,
passo 13 — "persistir log e evidências").

Uma única operação (`persist_query`) grava `QueryLog` + todas as
`QueryEvidence` juntas, mesmo racional de `DocumentRepositoryPort.
create_document` (RAG-021) persistir `Document`+`DocumentVersion`+
`IndexJob` numa única chamada: as evidências de uma consulta só fazem
sentido associadas a um `query_id` que ainda não existe antes deste
método rodar — não haveria como o caso de uso persistir os dois em
duas chamadas separadas sem já ter o id gerado por esta porta.

`evidence` sempre carrega TODAS as evidências recuperadas (RAG-034),
não só as que entraram no contexto do modelo (RAG-041) — RAG-061
(avaliação de retrieval, Recall@K/MRR) precisa do ranking completo que
a recuperação produziu para esta consulta, independente de quantas
evidências couberam no orçamento de tokens da geração."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from packages.domain.entities.query_log import QueryLog, TokenUsage


@dataclass(frozen=True, slots=True)
class QueryEvidenceInput:
    """Uma evidência a persistir junto de um `QueryLog` — o mesmo
    formato de `QueryEvidence` (seção 9 do plano) menos `query_id`
    (atribuído internamente por `persist_query`, já que as duas nascem
    juntas)."""

    chunk_id: UUID
    retrieval_score: float
    rerank_score: float | None
    position: int


class QueryRepositoryPort(ABC):
    """Porta hexagonal (seção 5.1 do plano): a camada de aplicação só
    conhece esta interface, nunca SQLAlchemy diretamente."""

    @abstractmethod
    async def persist_query(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        question_hash: str,
        model: str,
        latency_ms: int,
        token_usage: TokenUsage,
        trace_id: UUID,
        evidence: Sequence[QueryEvidenceInput],
    ) -> QueryLog:
        """Cria `QueryLog` (com `id` novo) e uma `QueryEvidence` por
        item de `evidence`, todos na mesma transação lógica. Devolve o
        `QueryLog` persistido (com `id` atribuído), para que quem chama
        monte `query_id` na resposta HTTP."""
