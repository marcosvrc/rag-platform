"""Endpoint de upload de documentos (RAG-021/RAG-022, seção 10.2 do plano).

Mesmo isolamento por tenant de `apps/api/routers/knowledge_bases.py`
(RAG-012/RAG-051): `tenant_id` vem de `get_current_tenant_id`
(resolvido a partir de um JWT autenticado) e é repassado explicitamente;
uma base de outro tenant (ou inexistente) retorna 404, nunca 403. Após
persistir documento/versão/job (RAG-021), o job é publicado na fila
(RAG-022, `get_job_queue`) para processamento assíncrono pelo
`apps/indexing_worker`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.document_repository import PostgresDocumentRepository
from adapters.object_storage.s3_object_storage import S3ObjectStorage
from adapters.postgres.engine import get_session
from adapters.queue.celery_job_queue import CeleryJobQueue
from apps.api.dependencies import (
    get_audit_log,
    get_current_identity,
    get_current_tenant_id,
    get_settings_dependency,
)
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from packages.application.commands import document as document_commands
from packages.application.ports.audit_log import AuditLogPort, record_audit_event_safely
from packages.application.ports.document_repository import (
    DocumentRepositoryPort,
    DocumentUpload,
    ReindexJob,
)
from packages.application.ports.job_queue import JobQueuePort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.object_storage import ObjectStoragePort
from packages.application.ports.token_verifier import TokenClaims
from packages.config.settings import Settings
from packages.contracts.document import DocumentUploadResponse, ReindexResponse
from packages.observability.metrics import (
    record_document_reindexed,
    record_document_uploaded,
)

router = APIRouter(prefix="/v1/knowledge-bases", tags=["documents"])


async def get_document_repository(
    session: AsyncSession = Depends(get_session),
) -> DocumentRepositoryPort:
    """`Depends()` próprio, mesmo padrão de
    `get_knowledge_base_repository` — os testes sobrescrevem via
    `app.dependency_overrides`."""
    return PostgresDocumentRepository(session)


async def get_object_storage(
    settings: Settings = Depends(get_settings_dependency),
) -> ObjectStoragePort:
    return S3ObjectStorage(settings)


async def get_job_queue(
    settings: Settings = Depends(get_settings_dependency),
) -> JobQueuePort:
    """`Depends()` próprio, mesmo padrão de `get_object_storage` — os
    testes sobrescrevem via `app.dependency_overrides` (RAG-022)."""
    return CeleryJobQueue(settings)


def _to_response(upload: DocumentUpload) -> DocumentUploadResponse:
    return DocumentUploadResponse(
        document_id=upload.document.id,
        knowledge_base_id=upload.document.knowledge_base_id,
        name=upload.document.name,
        mime_type=upload.document.mime_type,
        checksum=upload.document.checksum,
        document_status=upload.document.status,
        version=upload.version.version,
        index_job_id=upload.index_job.id,
        index_job_type=upload.index_job.type,
        index_job_status=upload.index_job.status,
        created_at=upload.document.created_at,
    )


def _to_reindex_response(
    result: ReindexJob, *, document_id: UUID, knowledge_base_id: UUID
) -> ReindexResponse:
    return ReindexResponse(
        document_id=document_id,
        knowledge_base_id=knowledge_base_id,
        version=result.version.version,
        index_job_id=result.index_job.id,
        index_job_type=result.index_job.type,
        index_job_status=result.index_job.status,
        created_at=result.version.created_at,
    )


@router.post(
    "/{knowledge_base_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    knowledge_base_id: UUID,
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tenant_id: UUID = Depends(get_current_tenant_id),
    identity: TokenClaims = Depends(get_current_identity),
    settings: Settings = Depends(get_settings_dependency),
    document_repository: DocumentRepositoryPort = Depends(get_document_repository),
    knowledge_base_repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
    object_storage: ObjectStoragePort = Depends(get_object_storage),
    job_queue: JobQueuePort = Depends(get_job_queue),
    audit_log: AuditLogPort = Depends(get_audit_log),
) -> DocumentUploadResponse:
    content = await file.read()
    upload = await document_commands.upload_document(
        document_repository,
        knowledge_base_repository,
        object_storage,
        job_queue,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        filename=file.filename or "",
        content_type=file.content_type or "",
        content=content,
        max_size_bytes=settings.document_max_size_bytes,
        idempotency_key=idempotency_key,
    )
    await record_audit_event_safely(
        audit_log,
        tenant_id=tenant_id,
        actor=identity.subject,
        action="document.upload",
        resource_type="document",
        resource_id=upload.document.id,
    )
    record_document_uploaded(mime_type=upload.document.mime_type)
    return _to_response(upload)


@router.post(
    "/{knowledge_base_id}/documents/{document_id}/reindex",
    response_model=ReindexResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reindex_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    identity: TokenClaims = Depends(get_current_identity),
    document_repository: DocumentRepositoryPort = Depends(get_document_repository),
    knowledge_base_repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
    job_queue: JobQueuePort = Depends(get_job_queue),
    audit_log: AuditLogPort = Depends(get_audit_log),
) -> ReindexResponse:
    result = await document_commands.reindex_document(
        document_repository,
        knowledge_base_repository,
        job_queue,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )
    await record_audit_event_safely(
        audit_log,
        tenant_id=tenant_id,
        actor=identity.subject,
        action="document.reindex",
        resource_type="document",
        resource_id=document_id,
    )
    record_document_reindexed()
    return _to_reindex_response(
        result, document_id=document_id, knowledge_base_id=knowledge_base_id
    )
