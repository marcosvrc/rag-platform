"""Adapter de object storage sobre MinIO/S3 (RAG-020).

Usa `aioboto3` (cliente S3 assíncrono, mesmo espírito do SQLAlchemy
assíncrono em `adapters/postgres/engine.py`, RAG-006) contra o endpoint
MinIO configurado em `packages/config/settings.py`. Funciona igual
contra um S3 de verdade — MinIO só muda o `endpoint_url` — por isso um
único adapter atende os dois, como a seção 5 do plano já modela
("MinIO local; interface compatível com S3").

`addressing_style="path"` é obrigatório para MinIO: o `virtual` padrão
do boto3 monta a URL como `<bucket>.<host>`, o que não resolve para um
host arbitrário como `localhost:9000`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import Any

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from packages.application.ports.object_storage import (
    ObjectNotFoundError,
    ObjectStoragePort,
    StoredObject,
    sanitize_object_key,
)
from packages.config.settings import Settings

_NOT_FOUND_ERROR_CODES = {"NoSuchKey", "404"}


class S3ObjectStorage(ObjectStoragePort):
    """Implementação real da porta, contra qualquer endpoint compatível
    com S3 — MinIO local hoje, potencialmente S3 de verdade depois."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session = aioboto3.Session()

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with self._session.client(
            "s3",
            endpoint_url=self._settings.minio_endpoint_url,
            aws_access_key_id=self._settings.minio_root_user,
            aws_secret_access_key=self._settings.minio_root_password.get_secret_value(),
            config=Config(s3={"addressing_style": "path"}),
        ) as client:
            yield client

    async def upload(self, *, key: str, content: bytes, content_type: str) -> StoredObject:
        sanitized_key = sanitize_object_key(key)
        checksum = sha256(content).hexdigest()
        async with self._client() as client:
            await client.put_object(
                Bucket=self._settings.minio_bucket,
                Key=sanitized_key,
                Body=content,
                ContentType=content_type,
            )
        return StoredObject(key=sanitized_key, checksum_sha256=checksum, size_bytes=len(content))

    async def download(self, *, key: str) -> bytes:
        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=self._settings.minio_bucket, Key=key)
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code")
                if error_code in _NOT_FOUND_ERROR_CODES:
                    raise ObjectNotFoundError(key) from exc
                raise
            body: bytes = await response["Body"].read()
        return body

    async def delete(self, *, key: str) -> None:
        async with self._client() as client:
            # DeleteObject do S3/MinIO já é idempotente (204 mesmo se a
            # key não existir), então nenhum tratamento extra é preciso
            # para cumprir "exclusão é idempotente" (contrato da porta).
            await client.delete_object(Bucket=self._settings.minio_bucket, Key=key)
