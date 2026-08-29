"""Estados do documento (seção 9.1 do plano)."""

from enum import StrEnum


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"
    DELETED = "DELETED"
