"""Entidade EvaluationRun (seção 9 do plano)."""

from typing import Any

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId, UtcDateTime
from packages.domain.enums.processing_status import ProcessingStatus


class EvaluationRun(DomainModel):
    id: EntityId
    dataset_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    metrics: dict[str, Any] = Field(default_factory=dict)
    status: ProcessingStatus
    created_at: UtcDateTime
