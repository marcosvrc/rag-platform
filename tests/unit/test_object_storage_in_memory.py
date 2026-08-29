"""Testes de RAG-020: `InMemoryObjectStorage` — mesmo contrato da porta
(`ObjectStoragePort`) que o adapter real de S3/MinIO, mas em memória."""

import hashlib

import pytest

from adapters.object_storage.in_memory import InMemoryObjectStorage
from packages.application.ports.object_storage import ObjectNotFoundError


@pytest.fixture
def storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


async def test_upload_then_download_roundtrips_the_exact_bytes(
    storage: InMemoryObjectStorage,
) -> None:
    content = b"conte\xc3\xbado bin\xc3\xa1rio de verdade, com acentos"

    stored = await storage.upload(
        key="docs/relatorio.pdf", content=content, content_type="application/pdf"
    )
    downloaded = await storage.download(key=stored.key)

    assert downloaded == content


async def test_upload_checksum_matches_a_fresh_sha256_of_the_content(
    storage: InMemoryObjectStorage,
) -> None:
    content = b"checksum precisa bater byte a byte"

    stored = await storage.upload(key="a.txt", content=content, content_type="text/plain")

    assert stored.checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert stored.size_bytes == len(content)


async def test_upload_sanitizes_the_key(storage: InMemoryObjectStorage) -> None:
    stored = await storage.upload(
        key="../etc/nome com espaço.pdf", content=b"x", content_type="application/pdf"
    )

    assert stored.key == "etc/nome_com_espaço.pdf"


async def test_download_of_a_missing_key_raises_object_not_found(
    storage: InMemoryObjectStorage,
) -> None:
    with pytest.raises(ObjectNotFoundError) as exc_info:
        await storage.download(key="nunca-existiu.pdf")

    assert exc_info.value.key == "nunca-existiu.pdf"


async def test_delete_is_idempotent(storage: InMemoryObjectStorage) -> None:
    stored = await storage.upload(key="a.txt", content=b"x", content_type="text/plain")

    await storage.delete(key=stored.key)
    await storage.delete(key=stored.key)  # segunda vez não levanta

    with pytest.raises(ObjectNotFoundError):
        await storage.download(key=stored.key)


async def test_content_type_is_preserved(storage: InMemoryObjectStorage) -> None:
    stored = await storage.upload(key="a.json", content=b"{}", content_type="application/json")

    assert storage.content_type_of(stored.key) == "application/json"
