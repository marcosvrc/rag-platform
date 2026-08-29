"""Adapters de `DocumentRepositoryPort` (RAG-021)."""

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.document_repository.postgres import PostgresDocumentRepository

__all__ = ["InMemoryDocumentRepository", "PostgresDocumentRepository"]
