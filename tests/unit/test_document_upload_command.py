"""Testes de RAG-021: `packages.application.commands.document.upload_document`.

Cobre os critérios de aceite da atividade: 202 (implícito — o comando
devolve o resultado que o router serializa com esse status), detecção
de duplicidade, rejeição de tipo/tamanho inválido e suporte a
idempotência. Usa os fakes em memória (`InMemoryDocumentRepository`,
`InMemoryKnowledgeBaseRepository`, `InMemoryObjectStorage`) — mesmo
padrão dos demais testes de comando neste projeto.

Nenhuma fixture aqui é `async def`: a base de conhecimento usada em
cada teste é criada por `_create_knowledge_base`, chamada explicitamente
no corpo do teste (não como fixture) — pytest-asyncio 0.23 (fixado em
`pyproject.toml`) é incompatível com fixtures assíncronas sob
pytest 9.1 (``AttributeError: 'FixtureDef' object has no attribute
'unittest'``); nenhum outro teste deste projeto usa fixture assíncrona,
então esse é o motivo desta escolha, não um padrão a seguir por gosto.
"""

from uuid import UUID, uuid4

import pytest

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.object_storage.in_memory import InMemoryObjectStorage
from packages.application.commands.document import upload_document
from packages.application.errors import ConflictError, NotFoundError, UnprocessableEntityError
from packages.application.ports.document_repository import (
    DocumentUpload,
    IdempotencyKeyConflictError,
)
from packages.domain.enums.document_status import DocumentStatus

TENANT_ID = uuid4()
MAX_SIZE = 1024


@pytest.fixture
def document_repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


@pytest.fixture
def object_storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


@pytest.fixture
def knowledge_base_repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


async def _create_knowledge_base(repository: InMemoryKnowledgeBaseRepository) -> UUID:
    knowledge_base = await repository.create(
        tenant_id=TENANT_ID, name="Manuais", description=None, config={}
    )
    return knowledge_base.id


async def _upload(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
    *,
    knowledge_base_id: UUID,
    filename: str = "guia.pdf",
    content_type: str = "application/pdf",
    content: bytes = b"%PDF-1.4 conteudo de teste",
    idempotency_key: str | None = None,
    max_size_bytes: int = MAX_SIZE,
) -> DocumentUpload:
    return await upload_document(
        document_repository,
        knowledge_base_repository,
        object_storage,
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base_id,
        filename=filename,
        content_type=content_type,
        content=content,
        max_size_bytes=max_size_bytes,
        idempotency_key=idempotency_key,
    )


async def test_upload_creates_pending_document_with_v1_and_pending_job(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    upload = await _upload(
        document_repository,
        knowledge_base_repository,
        object_storage,
        knowledge_base_id=knowledge_base_id,
    )

    assert upload.document.status == DocumentStatus.PENDING
    assert upload.document.knowledge_base_id == knowledge_base_id
    assert upload.version.version == 1
    assert upload.replayed is False
    stored_bytes = await object_storage.download(key=upload.version.object_key)
    assert stored_bytes == b"%PDF-1.4 conteudo de teste"


async def test_upload_to_unknown_knowledge_base_raises_not_found(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    with pytest.raises(NotFoundError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=uuid4(),
        )


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("guia.pdf", "application/pdf"),
        ("notas.md", "text/markdown"),
        ("leiame.txt", "text/plain"),
        (
            "relatorio.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ],
)
async def test_upload_accepts_every_supported_format(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
    filename: str,
    content_type: str,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    upload = await _upload(
        document_repository,
        knowledge_base_repository,
        object_storage,
        knowledge_base_id=knowledge_base_id,
        filename=filename,
        content_type=content_type,
    )
    assert upload.document.name == filename
    assert upload.document.mime_type == content_type


async def test_upload_rejects_unsupported_mime_type(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    with pytest.raises(UnprocessableEntityError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=knowledge_base_id,
            filename="virus.exe",
            content_type="application/x-msdownload",
        )


async def test_upload_rejects_extension_mismatched_with_mime_type(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    with pytest.raises(UnprocessableEntityError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=knowledge_base_id,
            filename="guia.txt",
            content_type="application/pdf",
        )


async def test_upload_rejects_empty_filename(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    with pytest.raises(UnprocessableEntityError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=knowledge_base_id,
            filename="   ",
        )


async def test_upload_rejects_empty_file(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    with pytest.raises(UnprocessableEntityError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=knowledge_base_id,
            content=b"",
        )


async def test_upload_rejects_file_larger_than_max_size(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    with pytest.raises(UnprocessableEntityError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=knowledge_base_id,
            content=b"x" * (MAX_SIZE + 1),
            max_size_bytes=MAX_SIZE,
        )


async def test_upload_detects_duplicate_checksum_in_the_same_knowledge_base(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    await _upload(
        document_repository,
        knowledge_base_repository,
        object_storage,
        knowledge_base_id=knowledge_base_id,
        content=b"conteudo identico",
    )

    with pytest.raises(ConflictError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=knowledge_base_id,
            filename="outro-nome.pdf",
            content=b"conteudo identico",
        )


async def test_upload_same_idempotency_key_and_same_content_replays_without_creating_new_document(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    first = await _upload(
        document_repository,
        knowledge_base_repository,
        object_storage,
        knowledge_base_id=knowledge_base_id,
        idempotency_key="retry-1",
    )
    assert first.replayed is False

    second = await _upload(
        document_repository,
        knowledge_base_repository,
        object_storage,
        knowledge_base_id=knowledge_base_id,
        idempotency_key="retry-1",
    )

    assert second.replayed is True
    assert second.document.id == first.document.id
    assert second.version.id == first.version.id
    assert second.index_job.id == first.index_job.id


async def test_upload_same_idempotency_key_with_different_content_raises_conflict(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    await _upload(
        document_repository,
        knowledge_base_repository,
        object_storage,
        knowledge_base_id=knowledge_base_id,
        idempotency_key="retry-2",
        content=b"conteudo original",
    )

    with pytest.raises(ConflictError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=knowledge_base_id,
            idempotency_key="retry-2",
            content=b"conteudo diferente, mesma chave",
        )


async def test_upload_without_idempotency_key_creates_a_new_document_each_time(
    document_repository: InMemoryDocumentRepository,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)

    first = await _upload(
        document_repository,
        knowledge_base_repository,
        object_storage,
        knowledge_base_id=knowledge_base_id,
        content=b"conteudo A",
    )
    second = await _upload(
        document_repository,
        knowledge_base_repository,
        object_storage,
        knowledge_base_id=knowledge_base_id,
        content=b"conteudo B",
    )

    assert first.document.id != second.document.id


class _RaceLosingDocumentRepository(InMemoryDocumentRepository):
    """Fake que simula a corrida rara descrita em
    `IdempotencyKeyConflictError`: mesmo sem repetição anterior visível
    (`find_idempotent_upload` continua devolvendo `None`), `create_document`
    descobre no commit que outra requisição venceu com uma chave
    diferente — cenário só reproduzível de verdade no adapter Postgres
    sob concorrência genuína (ver docstring do adapter)."""

    async def create_document(self, **kwargs: object) -> DocumentUpload:
        raise IdempotencyKeyConflictError(idempotency_key=str(kwargs.get("idempotency_key")))


async def test_upload_translates_idempotency_key_race_conflict_to_conflict_error(
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    object_storage: InMemoryObjectStorage,
) -> None:
    knowledge_base_id = await _create_knowledge_base(knowledge_base_repository)
    document_repository = _RaceLosingDocumentRepository()

    with pytest.raises(ConflictError):
        await _upload(
            document_repository,
            knowledge_base_repository,
            object_storage,
            knowledge_base_id=knowledge_base_id,
            idempotency_key="racy-key",
        )
