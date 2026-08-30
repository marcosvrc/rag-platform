"""Adapter Postgres de `QueryRepositoryPort` (RAG-044).

Mesma filosofia de `adapters/audit_log/postgres.py`: sem unit-of-work
compartilhada, este método comita sua própria transação — `QueryLog` e
todas as `QueryEvidence` nascem juntas, num único `commit()`."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from adapters.postgres.models.query_evidence import QueryEvidenceModel
from adapters.postgres.models.query_log import QueryLogModel
from packages.application.ports.query_repository import QueryEvidenceInput, QueryRepositoryPort
from packages.domain.entities.query_log import QueryLog, TokenUsage


def _to_entity(model: QueryLogModel) -> QueryLog:
    return QueryLog(
        id=model.id,
        tenant_id=model.tenant_id,
        knowledge_base_id=model.knowledge_base_id,
        question_hash=model.question_hash,
        model=model.model,
        latency_ms=model.latency_ms,
        token_usage=TokenUsage(input_tokens=model.input_tokens, output_tokens=model.output_tokens),
        trace_id=model.trace_id,
    )


class PostgresQueryRepository(QueryRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        query_log_model = QueryLogModel(
            id=uuid4(),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            question_hash=question_hash,
            model=model,
            latency_ms=latency_ms,
            input_tokens=token_usage.input_tokens,
            output_tokens=token_usage.output_tokens,
            trace_id=trace_id,
        )
        self._session.add(query_log_model)
        self._session.add_all(
            QueryEvidenceModel(
                query_id=query_log_model.id,
                chunk_id=item.chunk_id,
                retrieval_score=item.retrieval_score,
                rerank_score=item.rerank_score,
                position=item.position,
            )
            for item in evidence
        )
        await self._session.commit()
        return _to_entity(query_log_model)
