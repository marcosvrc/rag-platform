"""Entidade Chunk (seção 9 do plano)."""

from typing import Any

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId


class Chunk(DomainModel):
    id: EntityId
    tenant_id: EntityId
    knowledge_base_id: EntityId
    version_id: EntityId
    content: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    page: int | None = Field(default=None, ge=1)
    section: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = None
