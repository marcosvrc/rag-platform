"""Tabela `tenants` (RAG-011, entidade `Tenant` de RAG-010)."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Enum
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base
from packages.domain.enums.tenant_status import TenantStatus


class TenantModel(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        Enum(TenantStatus, name="tenant_status", native_enum=False, length=32),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
