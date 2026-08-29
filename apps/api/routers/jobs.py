"""Endpoint de status de job de indexação (RAG-027, seção 10.2 do plano).

Prometido desde o RAG-021 (docstring de
`DocumentUploadResponse`/`ReindexResponse`, `packages/contracts/document.py`):
o cliente recebe `index_job_id` na resposta de um upload ou de uma
reindexação e consulta este endpoint para saber se a indexação
terminou, e com que erro, se algum ("estados e erros são consultáveis",
critério de aceite do RAG-027).

Isolamento por tenant transitivo, mesmo padrão de
`packages/application/queries/document.py::get_index_job_status`: um
job de outro tenant (ou inexistente) sempre é 404, nunca 403.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from apps.api.dependencies import get_current_tenant_id
from apps.api.routers.documents import get_document_repository
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from packages.application.ports.document_repository import DocumentRepositoryPort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.queries import document as document_queries
from packages.contracts.document import IndexJobStatusResponse
from packages.domain.entities.index_job import IndexJob

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


def _to_response(job: IndexJob) -> IndexJobStatusResponse:
    return IndexJobStatusResponse(
        index_job_id=job.id,
        document_id=job.document_id,
        type=job.type,
        status=job.status,
        attempts=job.attempts,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/{index_job_id}", response_model=IndexJobStatusResponse)
async def get_index_job(
    index_job_id: UUID,
    tenant_id: UUID = Depends(get_current_tenant_id),
    document_repository: DocumentRepositoryPort = Depends(get_document_repository),
    knowledge_base_repository: KnowledgeBaseRepositoryPort = Depends(get_knowledge_base_repository),
) -> IndexJobStatusResponse:
    job = await document_queries.get_index_job_status(
        document_repository,
        knowledge_base_repository,
        tenant_id=tenant_id,
        index_job_id=index_job_id,
    )
    return _to_response(job)
