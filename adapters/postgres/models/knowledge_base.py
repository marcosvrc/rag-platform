"""Tabela `knowledge_bases` (RAG-011, entidade `KnowledgeBase` de RAG-010)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base
from packages.domain.enums.knowledge_base_status import KnowledgeBaseStatus


class KnowledgeBaseModel(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        # Isolamento por tenant (RAG-011): toda leitura/escrita de uma
        # base passa por tenant_id, então o índice cobre o filtro mais
        # comum do épico de gestão de bases (RAG-012).
        Index("ix_knowledge_bases_tenant_id", "tenant_id"),
        UniqueConstraint("tenant_id", "name", name="uq_knowledge_bases_tenant_id_name"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_knowledge_bases_tenant_id_tenants"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[KnowledgeBaseStatus] = mapped_column(
        Enum(KnowledgeBaseStatus, name="knowledge_base_status", native_enum=False, length=32),
        nullable=False,
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
