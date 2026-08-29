"""Consultas (leitura) de bases de conhecimento (RAG-012)."""

from __future__ import annotations

from uuid import UUID

from packages.application.errors import NotFoundError
from packages.application.ports.knowledge_base_repository import (
    KnowledgeBasePage,
    KnowledgeBaseRepositoryPort,
)
from packages.domain.entities.knowledge_base import KnowledgeBase

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


async def get_knowledge_base(
    repository: KnowledgeBaseRepositoryPort, *, tenant_id: UUID, knowledge_base_id: UUID
) -> KnowledgeBase:
    knowledge_base = await repository.get_by_id(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id
    )
    if knowledge_base is None:
        raise NotFoundError(detail="Base de conhecimento não encontrada.")
    return knowledge_base


async def list_knowledge_bases(
    repository: KnowledgeBaseRepositoryPort,
    *,
    tenant_id: UUID,
    cursor: str | None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> KnowledgeBasePage:
    bounded_limit = max(1, min(limit, MAX_PAGE_SIZE))
    return await repository.list_by_tenant(tenant_id=tenant_id, limit=bounded_limit, cursor=cursor)
