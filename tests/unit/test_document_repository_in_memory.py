"""Testes de RAG-021: `InMemoryDocumentRepository` — mesmo contrato da
porta (`DocumentRepositoryPort`) que o adapter Postgres real."""

from uuid import uuid4

import pytest

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from packages.application.ports.document_repository import DocumentChecksumConflictError
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
