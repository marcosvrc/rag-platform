"""Tabela `index_jobs` (RAG-011, entidade `IndexJob` de RAG-010)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.processing_status import ProcessingStatus


class IndexJobModel(Base):
    __tablename__ = "index_jobs"
    __table_args__ = (
        Index("ix_index_jobs_document_id", "document_id"),
        # O worker (RAG-022) consulta jobs pendentes/em execução por
        # status; sem esse índice, essa consulta faria table scan.
        Index("ix_index_jobs_status", "status"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", name="fk_index_jobs_document_id_documents", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[IndexJobType] = mapped_column(
        Enum(IndexJobType, name="index_job_type", native_enum=False, length=16),
        nullable=False,
    )
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status", native_enum=False, length=16),
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
