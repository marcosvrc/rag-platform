"""Modelos ORM (SQLAlchemy) do schema inicial (RAG-011).

Importar este pacote registra todas as tabelas em `Base.metadata` — é
por isso que `migrations/env.py` importa `adapters.postgres.models`
antes de usar `target_metadata` (senão o Alembic autogenerate não veria
nenhuma tabela).

Estes são modelos de persistência (adapter), não as entidades de domínio
de `packages/domain/entities` (RAG-010): a mesma regra de desacoplamento
da seção 5.1 do plano que proíbe o domínio de importar SQLAlchemy/
pgvector diretamente é o motivo de eles existirem aqui, em `adapters/`.
"""

from adapters.postgres.models.audit_event import AuditEventModel
from adapters.postgres.models.chunk import ChunkModel
from adapters.postgres.models.document import DocumentModel
from adapters.postgres.models.document_idempotency_key import DocumentIdempotencyKeyModel
from adapters.postgres.models.document_version import DocumentVersionModel
from adapters.postgres.models.evaluation_run import EvaluationRunModel
from adapters.postgres.models.feedback import FeedbackModel
from adapters.postgres.models.index_job import IndexJobModel
from adapters.postgres.models.knowledge_base import KnowledgeBaseModel
from adapters.postgres.models.query_evidence import QueryEvidenceModel
from adapters.postgres.models.query_log import QueryLogModel
from adapters.postgres.models.tenant import TenantModel

__all__ = [
    "AuditEventModel",
    "ChunkModel",
    "DocumentIdempotencyKeyModel",
    "DocumentModel",
    "DocumentVersionModel",
    "EvaluationRunModel",
    "FeedbackModel",
    "IndexJobModel",
    "KnowledgeBaseModel",
    "QueryEvidenceModel",
    "QueryLogModel",
    "TenantModel",
]
