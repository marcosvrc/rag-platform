"""Fake em memória de `DocumentRepositoryPort`, para testes
(RAG-021/RAG-022).

Espelha as mesmas regras do adapter Postgres (duplicidade por checksum,
idempotência por `Idempotency-Key`, isolamento por tenant, ciclo de
vida do `IndexJob`) sem precisar de um banco real — mesmo padrão de
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
    DocumentVersionConflictError,
    ReindexJob,
)
from packages.domain.entities.chunk import Chunk
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
        self._chunks: dict[UUID, Chunk] = {}

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

    async def claim_index_job(self, *, index_job_id: UUID) -> IndexJob | None:
        job = self._jobs.get(index_job_id)
        if job is None or job.status != ProcessingStatus.PENDING:
            return None
        claimed = job.model_copy(
            update={"status": ProcessingStatus.RUNNING, "updated_at": datetime.now(UTC)}
        )
        self._jobs[index_job_id] = claimed
        return claimed

    async def mark_index_job_succeeded(self, *, index_job_id: UUID) -> None:
        job = self._jobs[index_job_id]
        self._jobs[index_job_id] = job.model_copy(
            update={"status": ProcessingStatus.SUCCEEDED, "updated_at": datetime.now(UTC)}
        )

    async def get_index_job(self, *, index_job_id: UUID) -> IndexJob | None:
        return self._jobs.get(index_job_id)

    async def get_document(self, *, document_id: UUID) -> Document | None:
        return self._documents.get(document_id)

    async def get_latest_version(self, *, document_id: UUID) -> DocumentVersion | None:
        versions = [v for v in self._versions.values() if v.document_id == document_id]
        if not versions:
            return None
        return max(versions, key=lambda v: v.version)

    async def mark_document_processing(self, *, document_id: UUID) -> None:
        document = self._documents.get(document_id)
        if document is None or document.status == DocumentStatus.PROCESSING:
            return
        self._documents[document_id] = document.transition_to(DocumentStatus.PROCESSING)

    async def persist_chunks_and_activate_version(
        self,
        *,
        document_id: UUID,
        version_id: UUID,
        extracted_object_key: str,
        chunks: list[Chunk],
    ) -> None:
        # idempotente: descarta quaisquer chunks anteriores desta
        # version_id antes de gravar os novos (nunca duplica).
        stale_ids = [
            chunk_id for chunk_id, chunk in self._chunks.items() if chunk.version_id == version_id
        ]
        for chunk_id in stale_ids:
            del self._chunks[chunk_id]
        for chunk in chunks:
            self._chunks[chunk.id] = chunk

        version = self._versions[version_id]
        self._versions[version_id] = version.model_copy(
            update={"extracted_object_key": extracted_object_key}
        )

        document = self._documents[document_id]
        processing = (
            document
            if document.status == DocumentStatus.PROCESSING
            else document.transition_to(DocumentStatus.PROCESSING)
        )
        activated = processing.transition_to(DocumentStatus.INDEXED)
        self._documents[document_id] = activated.model_copy(
            update={"active_version_id": version_id}
        )

    async def mark_index_job_failed(
        self,
        *,
        index_job_id: UUID,
        attempts: int,
        error_code: str,
        error_message: str,
        final: bool,
    ) -> None:
        job = self._jobs[index_job_id]
        self._jobs[index_job_id] = job.model_copy(
            update={
                "status": ProcessingStatus.FAILED if final else ProcessingStatus.RUNNING,
                "attempts": attempts,
                "error_code": error_code,
                "error_message": error_message,
                "updated_at": datetime.now(UTC),
            }
        )

    def chunks_for_version(self, *, version_id: UUID) -> list[Chunk]:
        """Helper apenas de teste: lista os chunks persistidos para uma
        versão, na ordem de inserção. Não faz parte de DocumentRepositoryPort."""
        return [chunk for chunk in self._chunks.values() if chunk.version_id == version_id]

    async def create_reindex_job(
        self, *, document_id: UUID, object_key: str, version: int
    ) -> ReindexJob:
        if any(
            v.document_id == document_id and v.version == version for v in self._versions.values()
        ):
            raise DocumentVersionConflictError(document_id=document_id, version=version)

        now = datetime.now(UTC)
        new_version = DocumentVersion(
            id=uuid4(),
            document_id=document_id,
            version=version,
            object_key=object_key,
            created_at=now,
        )
        index_job = IndexJob(
            id=uuid4(),
            document_id=document_id,
            type=IndexJobType.REINDEX,
            status=ProcessingStatus.PENDING,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        self._versions[new_version.id] = new_version
        self._jobs[index_job.id] = index_job
        return ReindexJob(version=new_version, index_job=index_job)
