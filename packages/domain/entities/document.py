"""Entidade Document e sua máquina de estados (seção 9.1 do plano).

As transições permitidas são exatamente as do diagrama da seção 9.1:

    PENDING -> PROCESSING -> INDEXED
                          -> FAILED
                          -> QUARANTINED
    INDEXED -> PROCESSING -> INDEXED
    Qualquer estado (exceto DELETED) -> DELETED

Nenhuma outra transição é permitida — em particular, FAILED e
QUARANTINED só têm DELETED como saída (o diagrama não lista um caminho
de retry a partir deles; se esse caminho vier a existir, é uma decisão
nova, com ADR próprio).
"""

from uuid import UUID

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId, UtcDateTime
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.exceptions.errors import InvalidStatusTransitionError

_ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.PENDING: frozenset({DocumentStatus.PROCESSING, DocumentStatus.DELETED}),
    DocumentStatus.PROCESSING: frozenset(
        {
            DocumentStatus.INDEXED,
            DocumentStatus.FAILED,
            DocumentStatus.QUARANTINED,
            DocumentStatus.DELETED,
        }
    ),
    DocumentStatus.INDEXED: frozenset({DocumentStatus.PROCESSING, DocumentStatus.DELETED}),
    DocumentStatus.FAILED: frozenset({DocumentStatus.DELETED}),
    DocumentStatus.QUARANTINED: frozenset({DocumentStatus.DELETED}),
    DocumentStatus.DELETED: frozenset(),
}


class Document(DomainModel):
    id: EntityId
    knowledge_base_id: EntityId
    name: str = Field(min_length=1)
    mime_type: str = Field(min_length=1)
    checksum: str = Field(min_length=1)
    status: DocumentStatus
    active_version_id: UUID | None = None
    created_at: UtcDateTime

    def transition_to(self, new_status: DocumentStatus) -> "Document":
        """Retorna uma nova instância com ``status=new_status``, se a
        transição for permitida a partir do estado atual; caso
        contrário levanta ``InvalidStatusTransitionError``."""
        allowed = _ALLOWED_TRANSITIONS[self.status]
        if new_status not in allowed:
            raise InvalidStatusTransitionError(
                entity="Document",
                current=self.status.value,
                attempted=new_status.value,
            )
        return self.model_copy(update={"status": new_status})
