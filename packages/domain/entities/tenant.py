"""Entidade Tenant (seção 9 do plano)."""

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId, UtcDateTime
from packages.domain.enums.tenant_status import TenantStatus


class Tenant(DomainModel):
    id: EntityId
    name: str = Field(min_length=1)
    status: TenantStatus
    created_at: UtcDateTime
