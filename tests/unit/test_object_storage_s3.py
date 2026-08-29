"""Testes de RAG-020: `S3ObjectStorage`.

O cliente `aioboto3` é mockado (`unittest.mock.AsyncMock`) em vez de
usado contra um MinIO/S3 real — o mesmo motivo de todo o resto deste
projeto (seção 1 do plano: nenhum teste de PR chama serviço externo
real). O que se prova aqui é que o adapter monta as chamadas certas
(bucket/key/body/content-type), sanitiza a key antes de enviar, calcula
o checksum sobre os bytes reais e traduz `ClientError` (`NoSuchKey`) em
`ObjectNotFoundError` da porta — não que o SDK da AWS funciona (isso é
responsabilidade da própria AWS/MinIO).
"""

import hashlib
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from botocore.exceptions import ClientError
from pydantic import SecretStr

from adapters.object_storage.s3_object_storage import S3ObjectStorage
from packages.application.ports.object_storage import ObjectNotFoundError
from packages.config.settings import Settings


def _make_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        POSTGRES_PASSWORD=SecretStr("x"),
        MINIO_ROOT_PASSWORD=SecretStr("x"),
        MINIO_BUCKET="test-bucket",
    )


def _patch_client(storage: S3ObjectStorage, fake_client: AsyncMock) -> None:
    @asynccontextmanager
    async def _fake_client_cm() -> Any:
        yield fake_client

    storage._client = _fake_client_cm  # type: ignore[method-assign]


async def test_upload_calls_put_object_with_sanitized_key_and_content_type() -> None:
    storage = S3ObjectStorage(_make_settings())
    fake_client = AsyncMock()
    _patch_client(storage, fake_client)
    content = b"ola mundo"

    stored = await storage.upload(
        key="pasta insegura/../arquivo.txt", content=content, content_type="text/plain"
    )

    fake_client.put_object.assert_awaited_once_with(
        Bucket="test-bucket",
        Key="pasta_insegura/arquivo.txt",
        Body=content,
        ContentType="text/plain",
    )
    assert stored.key == "pasta_insegura/arquivo.txt"
    assert stored.checksum_sha256 == hashlib.sha256(content).hexdigest()
    assert stored.size_bytes == len(content)


async def test_download_returns_the_bytes_from_the_response_body() -> None:
    storage = S3ObjectStorage(_make_settings())
    fake_client = AsyncMock()
    fake_body = AsyncMock()
    fake_body.read.return_value = b"conteudo baixado"
    fake_client.get_object.return_value = {"Body": fake_body}
    _patch_client(storage, fake_client)

    content = await storage.download(key="arquivo.txt")

    fake_client.get_object.assert_awaited_once_with(Bucket="test-bucket", Key="arquivo.txt")
    assert content == b"conteudo baixado"


async def test_download_of_a_missing_key_raises_object_not_found() -> None:
    storage = S3ObjectStorage(_make_settings())
    fake_client = AsyncMock()
    fake_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject"
    )
    _patch_client(storage, fake_client)

    with pytest.raises(ObjectNotFoundError) as exc_info:
        await storage.download(key="nunca-existiu.txt")

    assert exc_info.value.key == "nunca-existiu.txt"


async def test_download_reraises_other_client_errors_unchanged() -> None:
    storage = S3ObjectStorage(_make_settings())
    fake_client = AsyncMock()
    fake_client.get_object.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "GetObject"
    )
    _patch_client(storage, fake_client)

    with pytest.raises(ClientError):
        await storage.download(key="algo.txt")


async def test_delete_calls_delete_object() -> None:
    storage = S3ObjectStorage(_make_settings())
    fake_client = AsyncMock()
    _patch_client(storage, fake_client)

    await storage.delete(key="arquivo.txt")

    fake_client.delete_object.assert_awaited_once_with(Bucket="test-bucket", Key="arquivo.txt")
