"""Tabela `query_logs` (RAG-011, entidade `QueryLog` de RAG-010).

O objeto de valor `TokenUsage` (`input_tokens`/`output_tokens`) é
achatado em duas colunas — não vira uma tabela própria, pois só existe
como parte de um `QueryLog`.
"""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base


class QueryLogModel(Base):
    __tablename__ = "query_logs"
    __table_args__ = (
        # Isolamento por tenant (RAG-011): mesma justificativa de `chunks`.
        Index("ix_query_logs_tenant_id", "tenant_id"),
        Index("ix_query_logs_knowledge_base_id", "knowledge_base_id"),
        CheckConstraint("latency_ms >= 0", name="latency_ms_non_negative"),
        CheckConstraint("input_tokens >= 0", name="input_tokens_non_negative"),
        CheckConstraint("output_tokens >= 0", name="output_tokens_non_negative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_query_logs_tenant_id_tenants"), nullable=False
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", name="fk_query_logs_knowledge_base_id_knowledge_bases"),
        nullable=False,
    )
    question_hash: Mapped[str] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(nullable=False)
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    input_tokens: Mapped[int] = mapped_column(nullable=False)
    output_tokens: Mapped[int] = mapped_column(nullable=False)
    trace_id: Mapped[UUID] = mapped_column(nullable=False)
