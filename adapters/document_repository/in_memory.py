"""Fake em memória de `DocumentRepositoryPort`, para testes (RAG-021).

Espelha as mesmas regras do adapter Postgres (duplicidade por checksum,
idempotência por `Idempotency-Key`, isolamento por tenant) sem precisar
de um banco real — mesmo padrão de
`adapters/knowledge_base_repository/in_memory.py` (RAG-012).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from packages.application.ports.document_repository import (
    DocumentChecksumConflictError,
    DocumentRepositoryPort,
    DocumentUpload,
)
from packages.domain.entities.document import Document
from packages.domain.entities.document_version import DocumentVersion
from packages.domain.entities.index_job import IndexJob
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.processing_status import ProcessingStatus


class InMemoryDocumentRepository(DocumentRepositoryPort):
    def __init__(self) -> None:
        self._documents: dict[UUID, Document] = {}
        self._versions: dict[UUID, DocumentVersion] = {}
        self._jobs: dict[UUID, IndexJob] = {}
        # (tenant_id, knowledge_base_id, idempotency_key) -> DocumentUpload original.
        self._idempotency_keys: dict[tuple[UUID, UUID, str], DocumentUpload] = {}

    async def find_by_checksum(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, checksum: str
    ) -> Document | None:
        del tenant_id  # Document não carrega tenant_id próprio (seção 9 do plano);
        # o isolamento é transitivo via knowledge_base_id, igual ao Postgres real.
        for document in self._documents.values():
            if (
                document.knowledge_base_id == knowledge_base_id
                and document.checksum == checksum
                and document.status != DocumentStatus.DELETED
            ):
                return document
        return None

    async def find_idempotent_upload(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, idempotency_key: str
    ) -> DocumentUpload | None:
        stored = self._idempotency_keys.get((tenant_id, knowledge_base_id, idempotency_key))
        return replace(stored, replayed=True) if stored is not None else None

    async def create_document(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        name: str,
        mime_type: str,
        checksum: str,
        object_key: str,
        idempotency_key: str | None,
    ) -> DocumentUpload:
        existing = await self.find_by_checksum(
            tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, checksum=checksum
        )
        if existing is not None:
            raise DocumentChecksumConflictError(
                knowledge_base_id=knowledge_base_id, existing_document_id=existing.id
            )

        now = datetime.now(UTC)
        document = Document(
            id=uuid4(),
            knowledge_base_id=knowledge_base_id,
            name=name,
            mime_type=mime_type,
            checksum=checksum,
            status=DocumentStatus.PENDING,
            created_at=now,
        )
        version = DocumentVersion(
            id=uuid4(), document_id=document.id, version=1, object_key=object_key, created_at=now
        )
        index_job = IndexJob(
            id=uuid4(),
            document_id=document.id,
            type=IndexJobType.INDEX,
            status=ProcessingStatus.PENDING,
            attempts=0,
            created_at=now,
            updated_at=now,
        )

        self._documents[document.id] = document
        self._versions[version.id] = version
        self._jobs[index_job.id] = index_job

        upload = DocumentUpload(
            document=document, version=version, index_job=index_job, replayed=False
        )
        if idempotency_key is not None:
            self._idempotency_keys[(tenant_id, knowledge_base_id, idempotency_key)] = upload
        return upload
