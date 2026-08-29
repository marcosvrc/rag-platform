"""Tabela `document_versions` (RAG-011, entidade `DocumentVersion` de RAG-010)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base


class DocumentVersionModel(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        Index("ix_document_versions_document_id", "document_id"),
        UniqueConstraint("document_id", "version", name="uq_document_versions_document_id_version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "documents.id", name="fk_document_versions_document_id_documents", ondelete="CASCADE"
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(nullable=False)
    object_key: Mapped[str] = mapped_column(nullable=False)
    extracted_object_key: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
