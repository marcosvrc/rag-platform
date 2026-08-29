"""Entidade IndexJob (seção 9 do plano).

O plano lista o campo como "timestamps" (plural, sem detalhar); aqui
isso é interpretado como ``created_at`` + ``updated_at``, o par mínimo
necessário para acompanhar quando um job foi criado e quando seu status
mudou pela última vez.
"""

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId, UtcDateTime
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.processing_status import ProcessingStatus


class IndexJob(DomainModel):
    id: EntityId
    document_id: EntityId
    type: IndexJobType
    status: ProcessingStatus
    attempts: int = Field(default=0, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
