"""Entidade KnowledgeBase (seção 9 do plano)."""

from typing import Any

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId, UtcDateTime
from packages.domain.enums.knowledge_base_status import KnowledgeBaseStatus


class KnowledgeBase(DomainModel):
    id: EntityId
    tenant_id: EntityId
    name: str = Field(min_length=1)
    description: str | None = None
    status: KnowledgeBaseStatus
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDateTime
    updated_at: UtcDateTime
