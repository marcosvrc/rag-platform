"""Contratos HTTP de documentos (RAG-021, seção 10.2 do plano).

Separado das entidades de domínio `Document`/`DocumentVersion`/
`IndexJob` pelo mesmo motivo de `packages/contracts/knowledge_base.py`
(RAG-012): o contrato é o formato estável exposto ao cliente, livre
para divergir de como o domínio evolui internamente.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from packages.domain.enums.document_status import DocumentStatus
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.processing_status import ProcessingStatus


class DocumentUploadResponse(BaseModel):
    """Corpo de `202 Accepted` de `POST /v1/knowledge-bases/{id}/documents`.

    A indexação em si (extração, chunking, embeddings — RAG-022+) é
    assíncrona; este contrato só confirma o que foi aceito e criado
    (documento, versão 1, job de indexação pendente), nunca o resultado
    da indexação — isso é `GET /v1/jobs/{job_id}` (RAG-027).
    """

    document_id: UUID
    knowledge_base_id: UUID
    name: str
    mime_type: str
    checksum: str
    document_status: DocumentStatus
    version: int
    index_job_id: UUID
    index_job_type: IndexJobType
    index_job_status: ProcessingStatus
    created_at: datetime
