"""Enumerações do domínio (estados de documento, tipos de job, etc.)."""

from packages.domain.enums.document_status import DocumentStatus
from packages.domain.enums.feedback_rating import FeedbackRating
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.knowledge_base_status import KnowledgeBaseStatus
from packages.domain.enums.processing_status import ProcessingStatus
from packages.domain.enums.tenant_status import TenantStatus

__all__ = [
    "DocumentStatus",
    "FeedbackRating",
    "IndexJobType",
    "KnowledgeBaseStatus",
    "ProcessingStatus",
    "TenantStatus",
]
