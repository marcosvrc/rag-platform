"""Fake em memória de `QueryRepositoryPort`, para testes (RAG-044) —
mesmo padrão de `adapters/document_repository/in_memory.py`."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

from packages.application.ports.query_repository import QueryEvidenceInput, QueryRepositoryPort
from packages.domain.entities.query_evidence import QueryEvidence
from packages.domain.entities.query_log import QueryLog, TokenUsage


class InMemoryQueryRepository(QueryRepositoryPort):
    def __init__(self) -> None:
        self.query_logs: dict[UUID, QueryLog] = {}
        # Lista simples (não indexada por query_id) — mesmo padrão de
        # `InMemoryAuditLog.events`: um atributo de inspeção só de
        # teste, nunca parte da porta abstrata.
        self.query_evidences: list[QueryEvidence] = []

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
        query_log = QueryLog(
            id=uuid4(),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            question_hash=question_hash,
            model=model,
            latency_ms=latency_ms,
            token_usage=token_usage,
            trace_id=trace_id,
        )
        self.query_logs[query_log.id] = query_log
        self.query_evidences.extend(
            QueryEvidence(
                query_id=query_log.id,
                chunk_id=item.chunk_id,
                retrieval_score=item.retrieval_score,
                rerank_score=item.rerank_score,
                position=item.position,
            )
            for item in evidence
        )
        return query_log
