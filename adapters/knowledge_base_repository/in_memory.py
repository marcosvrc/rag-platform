"""Fake em memória de `KnowledgeBaseRepositoryPort`, para testes (RAG-012).

Espelha as mesmas regras do adapter Postgres (unicidade de nome ACTIVE
por tenant, isolamento por tenant, exclusão lógica) sem precisar de um
banco real — mesmo padrão de `adapters/object_storage/in_memory.py`
(RAG-020).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from packages.application.ports.knowledge_base_repository import (
    KnowledgeBaseNameConflictError,
    KnowledgeBasePage,
    KnowledgeBaseRepositoryPort,
)
from packages.domain.entities.knowledge_base import KnowledgeBase
from packages.domain.enums.knowledge_base_status import KnowledgeBaseStatus

_MUTABLE_FIELDS = ("name", "description", "config")


def _cursor_key(knowledge_base: KnowledgeBase) -> tuple[datetime, UUID]:
    return (knowledge_base.created_at, knowledge_base.id)


def _encode_cursor(knowledge_base: KnowledgeBase) -> str:
    return f"{knowledge_base.created_at.isoformat()}|{knowledge_base.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    created_at_raw, _, id_raw = cursor.partition("|")
    return datetime.fromisoformat(created_at_raw), UUID(id_raw)


class InMemoryKnowledgeBaseRepository(KnowledgeBaseRepositoryPort):
    def __init__(self) -> None:
        self._by_id: dict[UUID, KnowledgeBase] = {}

    def _active_for_tenant(self, tenant_id: UUID) -> list[KnowledgeBase]:
        return [
            kb
            for kb in self._by_id.values()
            if kb.tenant_id == tenant_id and kb.status == KnowledgeBaseStatus.ACTIVE
        ]

    def _name_taken(self, tenant_id: UUID, name: str, *, exclude_id: UUID | None = None) -> bool:
        return any(
            kb.name == name and kb.id != exclude_id for kb in self._active_for_tenant(tenant_id)
        )

    async def create(
        self, *, tenant_id: UUID, name: str, description: str | None, config: dict[str, Any]
    ) -> KnowledgeBase:
        if self._name_taken(tenant_id, name):
            raise KnowledgeBaseNameConflictError(name)
        now = datetime.now(UTC)
        knowledge_base = KnowledgeBase(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name,
            description=description,
            status=KnowledgeBaseStatus.ACTIVE,
            config=config,
            created_at=now,
            updated_at=now,
        )
        self._by_id[knowledge_base.id] = knowledge_base
        return knowledge_base

    async def get_by_id(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> KnowledgeBase | None:
        knowledge_base = self._by_id.get(knowledge_base_id)
        if (
            knowledge_base is None
            or knowledge_base.tenant_id != tenant_id
            or knowledge_base.status != KnowledgeBaseStatus.ACTIVE
        ):
            return None
        return knowledge_base

    async def get_by_id_unscoped(self, *, knowledge_base_id: UUID) -> KnowledgeBase | None:
        return self._by_id.get(knowledge_base_id)

    async def list_by_tenant(
        self, *, tenant_id: UUID, limit: int, cursor: str | None
    ) -> KnowledgeBasePage:
        items = sorted(self._active_for_tenant(tenant_id), key=_cursor_key)
        if cursor is not None:
            after = _decode_cursor(cursor)
            items = [kb for kb in items if _cursor_key(kb) > after]
        page_items = items[:limit]
        next_cursor = _encode_cursor(page_items[-1]) if len(items) > limit else None
        return KnowledgeBasePage(items=page_items, next_cursor=next_cursor)

    async def update(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, fields: Mapping[str, Any]
    ) -> KnowledgeBase | None:
        current = await self.get_by_id(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id)
        if current is None:
            return None
        new_name = fields.get("name", current.name)
        if new_name != current.name and self._name_taken(
            tenant_id, new_name, exclude_id=current.id
        ):
            raise KnowledgeBaseNameConflictError(new_name)
        changes = {key: fields[key] for key in _MUTABLE_FIELDS if key in fields}
        updated = current.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
        self._by_id[updated.id] = updated
        return updated

    async def soft_delete(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> bool:
        current = await self.get_by_id(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id)
        if current is None:
            return False
        deleted = current.model_copy(
            update={"status": KnowledgeBaseStatus.DELETED, "updated_at": datetime.now(UTC)}
        )
        self._by_id[deleted.id] = deleted
        return True
