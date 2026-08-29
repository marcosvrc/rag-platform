"""Adapters de `KnowledgeBaseRepositoryPort` (RAG-012)."""

from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.knowledge_base_repository.postgres import PostgresKnowledgeBaseRepository

__all__ = ["InMemoryKnowledgeBaseRepository", "PostgresKnowledgeBaseRepository"]
