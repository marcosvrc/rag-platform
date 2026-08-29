"""Testes de RAG-027: `packages.application.commands.document.reindex_document`.

Cobre os critérios de aceite: reindexação cria nova versão (mesmo
object_key, número incrementado) e um novo IndexJob (tipo REINDEX,
publicado na fila); só é permitida quando o documento já está INDEXED;
consultas continuam disponíveis (o documento e a versão ativa anterior
não são tocados por esta chamada — só o worker, ao processar o novo
job, eventualmente ativa a versão nova, RAG-026)."""

from uuid import UUID, uuid4

import pytest

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.queue.in_memory import InMemoryJobQueue
from packages.application.commands.document import reindex_document
from packages.application.errors import ConflictError, NotFoundError
from packages.application.ports.document_repository import (
    DocumentVersionConflictError,
    ReindexJob,
)
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.processing_status import ProcessingStatus

TENANT_ID = uuid4()


@pytest.fixture
def document_repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


@pytest.fixture
def knowledge_base_repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


@pytest.fixture
def job_queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


async def _create_indexed_document(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    *,
    checksum: str,
) -> tuple[UUID, UUID]:
    """Cria uma KB + documento e o leva até INDEXED (via os métodos de
    RAG-026 já testados em outro lugar), simulando uma indexação inicial
    bem-sucedida. Retorna (knowledge_base_id, document_id)."""
    knowledge_base = await knowledge_base_repository.create(
        tenant_id=TENANT_ID, name=f"KB-{checksum[:8]}", description=None, config={}
    )
    upload = await document_repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base.id,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum=checksum,
        object_key=f"{knowledge_base.id}/{checksum}/guia.pdf",
        idempotency_key=None,
    )
    await document_repository.mark_document_processing(document_id=upload.document.id)
    await document_repository.persist_chunks_and_activate_version(
        document_id=upload.document.id,
        version_id=upload.version.id,
        extracted_object_key=f"{knowledge_base.id}/{upload.document.id}/v1/extracted.md",
        chunks=[],
    )
    return knowledge_base.id, upload.document.id


async def test_reindex_creates_version_2_and_reindex_job(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    job_queue: InMemoryJobQueue,
) -> None:
    knowledge_base_id, document_id = await _create_indexed_document(
        document_repository, knowledge_base_repository, checksum="a" * 64
    )
    original_version = await document_repository.get_latest_version(document_id=document_id)
    assert original_version is not None

    result = await reindex_document(
        document_repository,
        knowledge_base_repository,
        job_queue,
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )

    assert result.version.version == 2
    assert result.version.object_key == original_version.object_key
    assert result.index_job.type == IndexJobType.REINDEX
    assert result.index_job.status == ProcessingStatus.PENDING
    assert job_queue.enqueued_index_job_ids == [result.index_job.id]


async def test_reindex_does_not_touch_the_currently_active_version(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    job_queue: InMemoryJobQueue,
) -> None:
    """Critério de aceite "consultas continuam disponíveis": disparar a
    reindexação não muda o status do documento nem sua versão ativa —
    só o worker faz isso, depois de processar o novo job."""
    knowledge_base_id, document_id = await _create_indexed_document(
        document_repository, knowledge_base_repository, checksum="b" * 64
    )
    document_before = await document_repository.get_document(document_id=document_id)
    assert document_before is not None
    active_version_before = document_before.active_version_id

    await reindex_document(
        document_repository,
        knowledge_base_repository,
        job_queue,
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
    )

    document_after = await document_repository.get_document(document_id=document_id)
    assert document_after is not None
    assert document_after.status == DocumentStatus.INDEXED
    assert document_after.active_version_id == active_version_before


async def test_reindex_rejects_document_not_yet_indexed(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    job_queue: InMemoryJobQueue,
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

    with pytest.raises(ConflictError):
        await reindex_document(
            document_repository,
            knowledge_base_repository,
            job_queue,
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base.id,
            document_id=upload.document.id,
        )


async def test_reindex_unknown_knowledge_base_raises_not_found(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    job_queue: InMemoryJobQueue,
) -> None:
    with pytest.raises(NotFoundError):
        await reindex_document(
            document_repository,
            knowledge_base_repository,
            job_queue,
            tenant_id=TENANT_ID,
            knowledge_base_id=uuid4(),
            document_id=uuid4(),
        )


async def test_reindex_unknown_document_raises_not_found(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    job_queue: InMemoryJobQueue,
) -> None:
    knowledge_base = await knowledge_base_repository.create(
        tenant_id=TENANT_ID, name="Manuais", description=None, config={}
    )

    with pytest.raises(NotFoundError):
        await reindex_document(
            document_repository,
            knowledge_base_repository,
            job_queue,
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base.id,
            document_id=uuid4(),
        )


async def test_reindex_document_from_another_knowledge_base_raises_not_found(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    job_queue: InMemoryJobQueue,
) -> None:
    """Um documento existente, mas de outra base, nunca deve ser
    distinguível de um documento inexistente (mesmo padrão anti-403 do
    resto da API)."""
    _, document_id = await _create_indexed_document(
        document_repository, knowledge_base_repository, checksum="d" * 64
    )
    other_knowledge_base = await knowledge_base_repository.create(
        tenant_id=TENANT_ID, name="Outra base", description=None, config={}
    )

    with pytest.raises(NotFoundError):
        await reindex_document(
            document_repository,
            knowledge_base_repository,
            job_queue,
            tenant_id=TENANT_ID,
            knowledge_base_id=other_knowledge_base.id,
            document_id=document_id,
        )


async def test_reindex_another_tenants_knowledge_base_raises_not_found(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    job_queue: InMemoryJobQueue,
) -> None:
    knowledge_base_id, document_id = await _create_indexed_document(
        document_repository, knowledge_base_repository, checksum="e" * 64
    )

    with pytest.raises(NotFoundError):
        await reindex_document(
            document_repository,
            knowledge_base_repository,
            job_queue,
            tenant_id=uuid4(),
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )


class _AlwaysConflictingDocumentRepository(InMemoryDocumentRepository):
    """Wrapper de teste: simula a corrida rara descrita em
    `DocumentVersionConflictError` (RAG-027) — outra requisição
    concorrente cria a mesma versão entre o `get_latest_version` desta
    chamada e a criação de fato, algo que o fake em memória (de dono
    único, sem concorrência real) nunca produz sozinho."""

    async def create_reindex_job(
        self, *, document_id: UUID, object_key: str, version: int
    ) -> ReindexJob:
        raise DocumentVersionConflictError(document_id=document_id, version=version)


async def test_reindex_maps_version_conflict_to_409(
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    job_queue: InMemoryJobQueue,
) -> None:
    """O `DocumentVersionConflictError` do repositório (RAG-027) vira
    um 409 de aplicação, não uma exceção interna vazando para o
    cliente."""
    document_repository = _AlwaysConflictingDocumentRepository()
    knowledge_base_id, document_id = await _create_indexed_document(
        document_repository, knowledge_base_repository, checksum="f" * 64
    )

    with pytest.raises(ConflictError):
        await reindex_document(
            document_repository,
            knowledge_base_repository,
            job_queue,
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
        )
