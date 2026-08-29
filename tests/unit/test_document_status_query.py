"""Testes de RAG-027: `packages.application.queries.document.get_index_job_status`.

Cobre o isolamento por tenant transitivo (IndexJob -> Document ->
KnowledgeBase) e o critério de aceite "estados e erros são
consultáveis"."""

from uuid import uuid4

import pytest

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from packages.application.errors import NotFoundError
from packages.application.queries.document import get_index_job_status
from packages.domain.enums.processing_status import ProcessingStatus

TENANT_ID = uuid4()


@pytest.fixture
def document_repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


@pytest.fixture
def knowledge_base_repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


async def test_returns_the_job_for_its_own_tenant(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
) -> None:
    knowledge_base = await knowledge_base_repository.create(
        tenant_id=TENANT_ID, name="Manuais", description=None, config={}
    )
    upload = await document_repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base.id,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="a" * 64,
        object_key="kb/checksum/guia.pdf",
        idempotency_key=None,
    )

    job = await get_index_job_status(
        document_repository,
        knowledge_base_repository,
        tenant_id=TENANT_ID,
        index_job_id=upload.index_job.id,
    )

    assert job.id == upload.index_job.id
    assert job.status == ProcessingStatus.PENDING


async def test_reflects_failure_details_after_mark_index_job_failed(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
) -> None:
    knowledge_base = await knowledge_base_repository.create(
        tenant_id=TENANT_ID, name="Manuais", description=None, config={}
    )
    upload = await document_repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base.id,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="b" * 64,
        object_key="kb/checksum/guia.pdf",
        idempotency_key=None,
    )
    await document_repository.claim_index_job(index_job_id=upload.index_job.id)
    await document_repository.mark_index_job_failed(
        index_job_id=upload.index_job.id,
        attempts=5,
        error_code="DocumentParsingError",
        error_message="falha definitiva ao extrair conteúdo",
        final=True,
    )

    job = await get_index_job_status(
        document_repository,
        knowledge_base_repository,
        tenant_id=TENANT_ID,
        index_job_id=upload.index_job.id,
    )

    assert job.status == ProcessingStatus.FAILED
    assert job.error_code == "DocumentParsingError"
    assert job.error_message == "falha definitiva ao extrair conteúdo"


async def test_unknown_job_raises_not_found(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await get_index_job_status(
            document_repository,
            knowledge_base_repository,
            tenant_id=TENANT_ID,
            index_job_id=uuid4(),
        )


async def test_another_tenants_job_raises_not_found(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
) -> None:
    knowledge_base = await knowledge_base_repository.create(
        tenant_id=TENANT_ID, name="Manuais", description=None, config={}
    )
    upload = await document_repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base.id,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum="c" * 64,
        object_key="kb/checksum/guia.pdf",
        idempotency_key=None,
    )

    with pytest.raises(NotFoundError):
        await get_index_job_status(
            document_repository,
            knowledge_base_repository,
            tenant_id=uuid4(),
            index_job_id=upload.index_job.id,
        )
