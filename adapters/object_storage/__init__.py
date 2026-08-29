"""Adapter de armazenamento de objetos compatível com S3 (MinIO em desenvolvimento)."""

from adapters.object_storage.in_memory import InMemoryObjectStorage
from adapters.object_storage.s3_object_storage import S3ObjectStorage

__all__ = ["InMemoryObjectStorage", "S3ObjectStorage"]
