"""Entidade DocumentVersion (seção 9 do plano)."""

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId, UtcDateTime


class DocumentVersion(DomainModel):
    id: EntityId
    document_id: EntityId
    version: int = Field(ge=1)
    object_key: str = Field(min_length=1)
    extracted_object_key: str | None = None
    created_at: UtcDateTime
