"""Testes de RAG-021/RAG-022: `InMemoryDocumentRepository` — mesmo
contrato da porta (`DocumentRepositoryPort`) que o adapter Postgres
real, incluindo o ciclo de vida do `IndexJob` (RAG-022)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from packages.application.ports.document_repository import (
    DocumentChecksumConflictError,
    DocumentUpload,
)
from packages.domain.entities.chunk import Chunk
from packages.domain.entities.document_version import DocumentVersion
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.processing_status import ProcessingStatus

TENANT_ID = uuid4()
KNOWLEDGE_BASE_ID = uuid4()


@pytest.fixture
def repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


async def test_create_document_creates_pending_document_v1_and_pending_index_job(
    repository: InMemoryDocumentRepository,
) -> None:
    upload = await repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="a" * 64,
        object_key="kb/checksum/guia.pdf",
        idempotency_key=None,
    )

    assert upload.document.knowledge_base_id == KNOWLEDGE_BASE_ID
    assert upload.document.status == DocumentStatus.PENDING
    assert upload.version.version == 1
    assert upload.version.document_id == upload.document.id
    assert upload.index_job.document_id == upload.document.id
    assert upload.index_job.type == IndexJobType.INDEX
    assert upload.index_job.status == ProcessingStatus.PENDING
    assert upload.replayed is False


async def test_create_document_with_duplicate_checksum_raises_conflict(
    repository: InMemoryDocumentRepository,
) -> None:
    await repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="a" * 64,
        object_key="kb/checksum/guia.pdf",
        idempotency_key=None,
    )

    with pytest.raises(DocumentChecksumConflictError):
        await repository.create_document(
            tenant_id=TENANT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            name="outro-nome.pdf",
            mime_type="application/pdf",
            checksum="a" * 64,
            object_key="kb/checksum/outro-nome.pdf",
            idempotency_key=None,
        )


async def test_same_checksum_in_a_different_knowledge_base_is_not_a_duplicate(
    repository: InMemoryDocumentRepository,
) -> None:
    await repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="a" * 64,
        object_key="kb1/checksum/guia.pdf",
        idempotency_key=None,
    )

    other_kb = uuid4()
    upload = await repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=other_kb,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="a" * 64,
        object_key="kb2/checksum/guia.pdf",
        idempotency_key=None,
    )
    assert upload.document.knowledge_base_id == other_kb


async def test_find_by_checksum_returns_none_when_not_found(
    repository: InMemoryDocumentRepository,
) -> None:
    found = await repository.find_by_checksum(
        tenant_id=TENANT_ID, knowledge_base_id=KNOWLEDGE_BASE_ID, checksum="b" * 64
    )
    assert found is None


async def test_find_idempotent_upload_returns_none_when_key_never_used(
    repository: InMemoryDocumentRepository,
) -> None:
    found = await repository.find_idempotent_upload(
        tenant_id=TENANT_ID, knowledge_base_id=KNOWLEDGE_BASE_ID, idempotency_key="never-used"
    )
    assert found is None


async def test_create_document_with_idempotency_key_is_retrievable_and_marked_replayed(
    repository: InMemoryDocumentRepository,
) -> None:
    created = await repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="a" * 64,
        object_key="kb/checksum/guia.pdf",
        idempotency_key="key-1",
    )
    assert created.replayed is False

    found = await repository.find_idempotent_upload(
        tenant_id=TENANT_ID, knowledge_base_id=KNOWLEDGE_BASE_ID, idempotency_key="key-1"
    )
    assert found is not None
    assert found.replayed is True
    assert found.document.id == created.document.id
    assert found.version.id == created.version.id
    assert found.index_job.id == created.index_job.id


async def test_idempotency_key_is_scoped_per_tenant_and_knowledge_base(
    repository: InMemoryDocumentRepository,
) -> None:
    await repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="a" * 64,
        object_key="kb/checksum/guia.pdf",
        idempotency_key="shared-key",
    )

    found_other_tenant = await repository.find_idempotent_upload(
        tenant_id=uuid4(), knowledge_base_id=KNOWLEDGE_BASE_ID, idempotency_key="shared-key"
    )
    found_other_kb = await repository.find_idempotent_upload(
        tenant_id=TENANT_ID, knowledge_base_id=uuid4(), idempotency_key="shared-key"
    )

    assert found_other_tenant is None
    assert found_other_kb is None


async def _create_index_job(repository: InMemoryDocumentRepository) -> UUID:
    upload = await repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=KNOWLEDGE_BASE_ID,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum=str(uuid4()) + "a" * 28,
        object_key="kb/checksum/guia.pdf",
        idempotency_key=None,
    )
    return upload.index_job.id


class TestIndexJobLifecycle:
    """RAG-022: reivindicação (lock idempotente), sucesso e falha
    (com/sem tentativas restantes) de um `IndexJob`."""

    async def test_claim_index_job_transitions_pending_to_running(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        index_job_id = await _create_index_job(repository)

        claimed = await repository.claim_index_job(index_job_id=index_job_id)

        assert claimed is not None
        assert claimed.status == ProcessingStatus.RUNNING

    async def test_claim_index_job_twice_returns_none_on_the_second_call(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        index_job_id = await _create_index_job(repository)

        first = await repository.claim_index_job(index_job_id=index_job_id)
        second = await repository.claim_index_job(index_job_id=index_job_id)

        assert first is not None
        assert second is None

    async def test_claim_index_job_returns_none_for_unknown_id(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        claimed = await repository.claim_index_job(index_job_id=uuid4())
        assert claimed is None

    async def test_mark_index_job_succeeded_sets_status(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        index_job_id = await _create_index_job(repository)
        await repository.claim_index_job(index_job_id=index_job_id)

        await repository.mark_index_job_succeeded(index_job_id=index_job_id)

        job = repository._jobs[index_job_id]
        assert job.status == ProcessingStatus.SUCCEEDED

    async def test_mark_index_job_failed_not_final_keeps_status_running(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        index_job_id = await _create_index_job(repository)
        await repository.claim_index_job(index_job_id=index_job_id)

        await repository.mark_index_job_failed(
            index_job_id=index_job_id,
            attempts=1,
            error_code="RuntimeError",
            error_message="falha transitória",
            final=False,
        )

        job = repository._jobs[index_job_id]
        assert job.status == ProcessingStatus.RUNNING
        assert job.attempts == 1
        assert job.error_code == "RuntimeError"
        assert job.error_message == "falha transitória"

    async def test_mark_index_job_failed_final_sets_status_failed(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        index_job_id = await _create_index_job(repository)
        await repository.claim_index_job(index_job_id=index_job_id)

        await repository.mark_index_job_failed(
            index_job_id=index_job_id,
            attempts=5,
            error_code="RuntimeError",
            error_message="falha definitiva",
            final=True,
        )

        job = repository._jobs[index_job_id]
        assert job.status == ProcessingStatus.FAILED
        assert job.attempts == 5


class TestRag026PersistChunksAndActivateVersion:
    """RAG-026: getters auxiliares do worker (`get_index_job`,
    `get_document`, `get_latest_version`), `mark_document_processing`
    (idempotente) e `persist_chunks_and_activate_version` (atômico e
    idempotente)."""

    async def test_get_index_job_returns_none_for_unknown_id(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        assert await repository.get_index_job(index_job_id=uuid4()) is None

    async def test_get_index_job_returns_the_job(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        index_job_id = await _create_index_job(repository)
        job = await repository.get_index_job(index_job_id=index_job_id)
        assert job is not None
        assert job.id == index_job_id

    async def test_get_document_returns_none_for_unknown_id(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        assert await repository.get_document(document_id=uuid4()) is None

    async def test_get_document_returns_the_document(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        upload = await repository.create_document(
            tenant_id=TENANT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            name="guia.pdf",
            mime_type="application/pdf",
            checksum="c" * 64,
            object_key="kb/checksum/guia.pdf",
            idempotency_key=None,
        )
        found = await repository.get_document(document_id=upload.document.id)
        assert found is not None
        assert found.id == upload.document.id

    async def test_get_latest_version_returns_none_when_document_has_no_versions(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        assert await repository.get_latest_version(document_id=uuid4()) is None

    async def test_get_latest_version_returns_the_highest_version_number(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        upload = await repository.create_document(
            tenant_id=TENANT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            name="guia.pdf",
            mime_type="application/pdf",
            checksum="d" * 64,
            object_key="kb/checksum/guia.pdf",
            idempotency_key=None,
        )
        # Simula uma segunda versão sendo criada por uma reindexação futura
        # (RAG-027 ainda não existe) inserindo diretamente no fake.
        newer = DocumentVersion(
            id=uuid4(),
            document_id=upload.document.id,
            version=2,
            object_key="kb/checksum/guia-v2.pdf",
            created_at=datetime.now(UTC),
        )
        repository._versions[newer.id] = newer

        latest = await repository.get_latest_version(document_id=upload.document.id)
        assert latest is not None
        assert latest.version == 2
        assert latest.id == newer.id

    async def test_mark_document_processing_transitions_pending_to_processing(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        upload = await repository.create_document(
            tenant_id=TENANT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            name="guia.pdf",
            mime_type="application/pdf",
            checksum="e" * 64,
            object_key="kb/checksum/guia.pdf",
            idempotency_key=None,
        )

        await repository.mark_document_processing(document_id=upload.document.id)

        document = await repository.get_document(document_id=upload.document.id)
        assert document is not None
        assert document.status == DocumentStatus.PROCESSING

    async def test_mark_document_processing_is_idempotent_when_already_processing(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        upload = await repository.create_document(
            tenant_id=TENANT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            name="guia.pdf",
            mime_type="application/pdf",
            checksum="f" * 64,
            object_key="kb/checksum/guia.pdf",
            idempotency_key=None,
        )
        await repository.mark_document_processing(document_id=upload.document.id)

        # Não deve levantar InvalidStatusTransitionError na segunda chamada
        # (PROCESSING -> PROCESSING não é uma transição válida da FSM, mas
        # este método é especificamente idempotente).
        await repository.mark_document_processing(document_id=upload.document.id)

        document = await repository.get_document(document_id=upload.document.id)
        assert document is not None
        assert document.status == DocumentStatus.PROCESSING

    async def test_mark_document_processing_is_a_noop_for_unknown_document(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        # Defensivo: não deve levantar KeyError.
        await repository.mark_document_processing(document_id=uuid4())

    async def _prepare_processing_document(
        self, repository: InMemoryDocumentRepository, *, checksum: str
    ) -> DocumentUpload:
        upload = await repository.create_document(
            tenant_id=TENANT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            name="guia.pdf",
            mime_type="application/pdf",
            checksum=checksum,
            object_key="kb/checksum/guia.pdf",
            idempotency_key=None,
        )
        await repository.mark_document_processing(document_id=upload.document.id)
        return upload

    def _make_chunk(self, *, version_id: UUID, content: str) -> Chunk:
        return Chunk(
            id=uuid4(),
            tenant_id=TENANT_ID,
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            version_id=version_id,
            content=content,
            token_count=3,
            page=None,
            section=None,
            metadata={},
            embedding=[0.1, 0.2, 0.3],
        )

    async def test_persist_chunks_and_activate_version_activates_document_and_version(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        upload = await self._prepare_processing_document(repository, checksum="1" * 64)
        chunk = self._make_chunk(version_id=upload.version.id, content="olá mundo")

        await repository.persist_chunks_and_activate_version(
            document_id=upload.document.id,
            version_id=upload.version.id,
            extracted_object_key="kb/doc/v1/extracted.md",
            chunks=[chunk],
        )

        document = await repository.get_document(document_id=upload.document.id)
        version = await repository.get_latest_version(document_id=upload.document.id)
        assert document is not None
        assert document.status == DocumentStatus.INDEXED
        assert document.active_version_id == upload.version.id
        assert version is not None
        assert version.extracted_object_key == "kb/doc/v1/extracted.md"
        assert repository.chunks_for_version(version_id=upload.version.id) == [chunk]

    async def test_persist_chunks_and_activate_version_is_idempotent_and_never_duplicates(
        self, repository: InMemoryDocumentRepository
    ) -> None:
        upload = await self._prepare_processing_document(repository, checksum="2" * 64)
        first_chunk = self._make_chunk(version_id=upload.version.id, content="primeira versão")

        await repository.persist_chunks_and_activate_version(
            document_id=upload.document.id,
            version_id=upload.version.id,
            extracted_object_key="kb/doc/v1/extracted.md",
            chunks=[first_chunk],
        )

        # Reprocessamento: chunks diferentes para a MESMA version_id.
        await repository.mark_document_processing(document_id=upload.document.id)
        second_chunk = self._make_chunk(version_id=upload.version.id, content="reprocessado")
        await repository.persist_chunks_and_activate_version(
            document_id=upload.document.id,
            version_id=upload.version.id,
            extracted_object_key="kb/doc/v1/extracted.md",
            chunks=[second_chunk],
        )

        stored = repository.chunks_for_version(version_id=upload.version.id)
        assert stored == [second_chunk]

        document = await repository.get_document(document_id=upload.document.id)
        assert document is not None
        assert document.status == DocumentStatus.INDEXED
