"""Entidades do domínio (Tenant, KnowledgeBase, Document, Chunk, etc.)."""

from packages.domain.entities.base import DomainModel, EntityId, UtcDateTime
from packages.domain.entities.chunk import Chunk
from packages.domain.entities.document import Document
from packages.domain.entities.document_version import DocumentVersion
from packages.domain.entities.evaluation_run import EvaluationRun
from packages.domain.entities.feedback import Feedback
from packages.domain.entities.index_job import IndexJob
from packages.domain.entities.knowledge_base import KnowledgeBase
from packages.domain.entities.query_evidence import QueryEvidence
from packages.domain.entities.query_log import QueryLog, TokenUsage
from packages.domain.entities.tenant import Tenant

__all__ = [
    "Chunk",
    "Document",
    "DocumentVersion",
    "DomainModel",
    "EntityId",
    "EvaluationRun",
    "Feedback",
    "IndexJob",
    "KnowledgeBase",
    "QueryEvidence",
    "QueryLog",
    "Tenant",
    "TokenUsage",
    "UtcDateTime",
]
