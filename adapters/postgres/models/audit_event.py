"""Tabela `audit_events` (RAG-054, migration 0005).

Não corresponde a uma entidade de domínio (seção 9 do plano não lista
isso) — mesmo precedente de `document_idempotency_keys` (RAG-021):
infraestrutura de aplicação (trilho de auditoria), não um conceito do
domínio de RAG, então só existe aqui, nunca em
`packages/domain/entities`.

`resource_id` não é uma foreign key: é polimórfico (aponta para
`knowledge_bases.id` OU `documents.id`, dependendo de `resource_type`)
— uma FK exigiria uma tabela de destino única. `tenant_id` é FK normal
(um evento de auditoria sempre pertence a exatamente um tenant, sem
ambiguidade)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base


class AuditEventModel(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        # Toda consulta futura de auditoria (ex.: um painel
        # administrativo) filtra por tenant primeiro — mesmo padrão de
        # índice único por tenant_id usado em todas as outras tabelas
        # multi-tenant deste schema (RAG-011).
        Index("ix_audit_events_tenant_id", "tenant_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_audit_events_tenant_id_tenants"),
        nullable=False,
    )
    actor: Mapped[str] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(nullable=False)
    resource_type: Mapped[str] = mapped_column(nullable=False)
    resource_id: Mapped[UUID] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
