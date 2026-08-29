"""Tabela `document_idempotency_keys` (RAG-021, migration 0003).

Não corresponde a uma entidade de domínio (seção 9 do plano não lista
isso) — é puramente infraestrutura de aplicação para o requisito de
idempotência (seção 8: "endpoints de criação devem aceitar
Idempotency-Key"), então só existe aqui, nunca em
`packages/domain/entities`.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base


class DocumentIdempotencyKeyModel(Base):
    __tablename__ = "document_idempotency_keys"
    __table_args__ = (
        Index("ix_document_idempotency_keys_tenant_id", "tenant_id"),
        Index("ix_document_idempotency_keys_knowledge_base_id", "knowledge_base_id"),
        Index("ix_document_idempotency_keys_document_id", "document_id"),
        UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "idempotency_key",
            name="uq_document_idempotency_keys_tenant_id_knowledge_base_id_key",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_document_idempotency_keys_tenant_id_tenants"),
        nullable=False,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "knowledge_bases.id",
            name="fk_document_idempotency_keys_knowledge_base_id_knowledge_bases",
        ),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(nullable=False)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "documents.id",
            name="fk_document_idempotency_keys_document_id_documents",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name="fk_document_idempotency_keys_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    index_job_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "index_jobs.id",
            name="fk_document_idempotency_keys_index_job_id_index_jobs",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
