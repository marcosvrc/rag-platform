"""Comandos (escrita) de bases de conhecimento (RAG-012).

Orquestra a porta `KnowledgeBaseRepositoryPort` e traduz falhas
específicas do repositório (`KnowledgeBaseNameConflictError`) e
violações de regra de negócio para os erros de aplicação de RAG-013
(`packages/application/errors.py`), que `apps/api/errors.py` já sabe
converter em Problem Details.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from packages.application.errors import ConflictError, NotFoundError, UnprocessableEntityError
from packages.application.ports.knowledge_base_repository import (
    KnowledgeBaseNameConflictError,
    KnowledgeBaseRepositoryPort,
)
from packages.domain.entities.knowledge_base import KnowledgeBase

_ALLOWED_UPDATE_FIELDS = frozenset({"name", "description", "config"})
# `description` pode ser `None` explicitamente (limpa o campo); `name` e
# `config` não podem — o domínio exige nome não vazio e config é sempre
# um dict (nunca nulo).
_NON_NULLABLE_UPDATE_FIELDS = frozenset({"name", "config"})


async def create_knowledge_base(
    repository: KnowledgeBaseRepositoryPort,
    *,
    tenant_id: UUID,
    name: str,
    description: str | None,
    config: dict[str, Any],
) -> KnowledgeBase:
    try:
        return await repository.create(
            tenant_id=tenant_id, name=name, description=description, config=config
        )
    except KnowledgeBaseNameConflictError as exc:
        raise ConflictError(detail=str(exc)) from exc


async def update_knowledge_base(
    repository: KnowledgeBaseRepositoryPort,
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    fields: Mapping[str, Any],
) -> KnowledgeBase:
    unknown = set(fields) - _ALLOWED_UPDATE_FIELDS
    if unknown:
        raise ValueError(f"Campos de atualização não suportados: {sorted(unknown)}")
    for field_name in _NON_NULLABLE_UPDATE_FIELDS:
        if field_name in fields and fields[field_name] is None:
            raise UnprocessableEntityError(detail=f"O campo '{field_name}' não pode ser nulo.")

    try:
        updated = await repository.update(
            tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, fields=fields
        )
    except KnowledgeBaseNameConflictError as exc:
        raise ConflictError(detail=str(exc)) from exc
    if updated is None:
        raise NotFoundError(detail="Base de conhecimento não encontrada.")
    return updated


async def delete_knowledge_base(
    repository: KnowledgeBaseRepositoryPort, *, tenant_id: UUID, knowledge_base_id: UUID
) -> None:
    deleted = await repository.soft_delete(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id)
    if not deleted:
        raise NotFoundError(detail="Base de conhecimento não encontrada.")
