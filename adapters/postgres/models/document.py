"""Tabela `documents` (RAG-011, entidade `Document` de RAG-010).

`active_version_id` referencia `document_versions.id`, mas
`document_versions.document_id` referencia `documents.id` — as duas
tabelas se referenciam mutuamente. `use_alter=True` faz o Alembic emitir
a FK de `active_version_id` como um `ALTER TABLE` separado, executado
depois que as duas tabelas já existem (ver migration 0002).
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base
from packages.domain.enums.document_status import DocumentStatus


class DocumentModel(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_knowledge_base_id", "knowledge_base_id"),
        # Suporte à detecção de duplicidade no upload (RAG-021): o mesmo
        # checksum não pode ser reenviado duas vezes para a mesma base.
        UniqueConstraint(
            "knowledge_base_id", "checksum", name="uq_documents_knowledge_base_id_checksum"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", name="fk_documents_knowledge_base_id_knowledge_bases"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(nullable=False)
    mime_type: Mapped[str] = mapped_column(nullable=False)
    checksum: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status", native_enum=False, length=32),
        nullable=False,
    )
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name="fk_documents_active_version_id_document_versions",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
