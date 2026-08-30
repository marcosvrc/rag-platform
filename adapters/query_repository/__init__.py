"""Adapters de `QueryRepositoryPort` (RAG-044)."""

from adapters.query_repository.in_memory import InMemoryQueryRepository
from adapters.query_repository.postgres import PostgresQueryRepository

__all__ = ["InMemoryQueryRepository", "PostgresQueryRepository"]
