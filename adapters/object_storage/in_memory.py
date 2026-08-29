"""Adapter de object storage em memória (RAG-020).

Implementação de teste/desenvolvimento local da `ObjectStoragePort` —
os dados vivem só no processo (não é durável, nunca deve ser usada em
produção). Serve para testar casos de uso de ingestão (RAG-021+) sem
precisar de um MinIO/S3 real no ar.
"""

import hashlib
from dataclasses import dataclass

from packages.application.ports.object_storage import (
    ObjectNotFoundError,
    ObjectStoragePort,
    StoredObject,
    sanitize_object_key,
)


@dataclass(frozen=True, slots=True)
class _StoredContent:
    body: bytes
    content_type: str


class InMemoryObjectStorage(ObjectStoragePort):
    """Fake de `ObjectStoragePort` guardado em um dict de processo."""

    def __init__(self) -> None:
        self._objects: dict[str, _StoredContent] = {}

    async def upload(self, *, key: str, content: bytes, content_type: str) -> StoredObject:
        sanitized_key = sanitize_object_key(key)
        self._objects[sanitized_key] = _StoredContent(body=content, content_type=content_type)
        return StoredObject(
            key=sanitized_key,
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    async def download(self, *, key: str) -> bytes:
        stored = self._objects.get(key)
        if stored is None:
            raise ObjectNotFoundError(key)
        return stored.body

    async def delete(self, *, key: str) -> None:
        # Exclusão é idempotente (contrato da porta): remover uma key
        # que não existe não é erro.
        self._objects.pop(key, None)

    def content_type_of(self, key: str) -> str | None:
        """Só para testes: inspeciona o `content_type` armazenado."""
        stored = self._objects.get(key)
        return stored.content_type if stored is not None else None
