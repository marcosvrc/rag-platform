"""Testes de RAG-026: `PipelineDocumentProcessor` — orquestração do
pipeline completo de indexação (extração, chunking, embeddings,
persistência atômica e ativação de versão), usando fakes para as 5
portas envolvidas (nenhum Docling/LiteLLM/Postgres/MinIO real)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from adapters.document_processor.pipeline import (
    DocumentNotFoundForIndexJobError,
    DocumentVersionNotFoundError,
    MissingKnowledgeBaseForDocumentError,
    PipelineDocumentProcessor,
)
from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.object_storage.in_memory import InMemoryObjectStorage
from packages.application.ports.document_parser import DocumentParserPort, ParsedDocument
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.domain.entities.index_job import IndexJob
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.enums.index_job_type import IndexJobType
from packages.domain.enums.processing_status import ProcessingStatus

TENANT_ID = uuid4()

_MARKDOWN = (
    "# Seção 1\n\n"
    "Este é o primeiro parágrafo do documento de teste, com conteúdo "
    "suficiente para virar ao menos um chunk depois da divisão.\n\n"
    "Este é o segundo parágrafo, também com texto normal para compor "
    "o mesmo chunk ou o seguinte, dependendo do tamanho configurado.\n"
)


class FakeDocumentParser(DocumentParserPort):
    def __init__(self, *, markdown: str = _MARKDOWN) -> None:
        self._markdown = markdown
        self.calls: list[tuple[str, str]] = []

    async def parse(self, *, filename: str, content: bytes, content_type: str) -> ParsedDocument:
        del content
        self.calls.append((filename, content_type))
        return ParsedDocument(
            markdown=self._markdown, page_count=None, original_mimetype=content_type
        )


class FakeEmbeddingProvider(EmbeddingProviderPort):
    def __init__(self) -> None:
        self.embedded_texts: list[list[str]] = []

    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        self.embedded_texts.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]


class Fixture:
    def __init__(self) -> None:
        self.document_repository = InMemoryDocumentRepository()
        self.knowledge_base_repository = InMemoryKnowledgeBaseRepository()
        self.object_storage = InMemoryObjectStorage()
        self.parser = FakeDocumentParser()
        self.embedding_provider = FakeEmbeddingProvider()
        self.processor = PipelineDocumentProcessor(
            document_repository=self.document_repository,
            knowledge_base_repository=self.knowledge_base_repository,
            object_storage=self.object_storage,
            document_parser=self.parser,
            embedding_provider=self.embedding_provider,
        )

    async def create_ready_job(self, *, checksum: str) -> tuple[UUID, UUID, UUID]:
        """Cria uma KnowledgeBase, sobe o conteúdo original no object
        storage e cria o Document/DocumentVersion/IndexJob associados.
        Retorna (index_job_id, document_id, version_id)."""
        kb = await self.knowledge_base_repository.create(
            tenant_id=TENANT_ID, name=f"KB-{checksum[:8]}", description=None, config={}
        )
        object_key = f"{kb.id}/{checksum}/guia.md"
        await self.object_storage.upload(
            key=object_key, content=b"conteudo original", content_type="text/markdown"
        )
        upload = await self.document_repository.create_document(
            tenant_id=TENANT_ID,
            knowledge_base_id=kb.id,
            name="guia.md",
            mime_type="text/markdown",
            checksum=checksum,
            object_key=object_key,
            idempotency_key=None,
        )
        return upload.index_job.id, upload.document.id, upload.version.id


@pytest.fixture
def fx() -> Fixture:
    return Fixture()


async def test_process_persists_chunks_and_activates_version_end_to_end(fx: Fixture) -> None:
    index_job_id, document_id, version_id = await fx.create_ready_job(checksum="a" * 64)

    await fx.processor.process(index_job_id=index_job_id)

    document = await fx.document_repository.get_document(document_id=document_id)
    version = await fx.document_repository.get_latest_version(document_id=document_id)
    assert document is not None
    assert document.status == DocumentStatus.INDEXED
    assert document.active_version_id == version_id
    assert version is not None
    assert version.extracted_object_key is not None

    chunks = fx.document_repository.chunks_for_version(version_id=version_id)
    assert len(chunks) >= 1
    assert all(chunk.tenant_id == TENANT_ID for chunk in chunks)
    assert all(chunk.embedding == [0.1, 0.2, 0.3] for chunk in chunks)

    extracted_content = await fx.object_storage.download(key=version.extracted_object_key)
    assert extracted_content.decode("utf-8") == _MARKDOWN
    assert fx.parser.calls == [("guia.md", "text/markdown")]


async def test_process_is_idempotent_on_reprocessing(fx: Fixture) -> None:
    index_job_id, document_id, version_id = await fx.create_ready_job(checksum="b" * 64)

    await fx.processor.process(index_job_id=index_job_id)
    first_chunks = fx.document_repository.chunks_for_version(version_id=version_id)

    await fx.processor.process(index_job_id=index_job_id)
    second_chunks = fx.document_repository.chunks_for_version(version_id=version_id)

    # Mesmo número de chunks (nunca duplica) e documento continua INDEXED.
    assert len(second_chunks) == len(first_chunks)
    document = await fx.document_repository.get_document(document_id=document_id)
    assert document is not None
    assert document.status == DocumentStatus.INDEXED
    assert document.active_version_id == version_id


async def test_process_returns_silently_when_index_job_is_missing(fx: Fixture) -> None:
    # Defensivo: job sumiu entre o worker reivindicá-lo e o processamento.
    await fx.processor.process(index_job_id=uuid4())  # não deve levantar.


async def test_process_raises_when_document_is_missing(fx: Fixture) -> None:
    now = datetime.now(UTC)
    orphan_job = IndexJob(
        id=uuid4(),
        document_id=uuid4(),
        type=IndexJobType.INDEX,
        status=ProcessingStatus.RUNNING,
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    fx.document_repository._jobs[orphan_job.id] = orphan_job

    with pytest.raises(DocumentNotFoundForIndexJobError):
        await fx.processor.process(index_job_id=orphan_job.id)


async def test_process_raises_when_document_has_no_version(fx: Fixture) -> None:
    index_job_id, document_id, version_id = await fx.create_ready_job(checksum="c" * 64)
    del fx.document_repository._versions[version_id]

    with pytest.raises(DocumentVersionNotFoundError):
        await fx.processor.process(index_job_id=index_job_id)


async def test_process_raises_when_knowledge_base_is_missing(fx: Fixture) -> None:
    index_job_id, document_id, _ = await fx.create_ready_job(checksum="d" * 64)
    document = await fx.document_repository.get_document(document_id=document_id)
    assert document is not None
    orphan_document = document.model_copy(update={"knowledge_base_id": uuid4()})
    fx.document_repository._documents[document_id] = orphan_document

    with pytest.raises(MissingKnowledgeBaseForDocumentError):
        await fx.processor.process(index_job_id=index_job_id)
