"""Adapter Postgres de `KnowledgeBaseRepositoryPort` (RAG-012).

Cada método comita sua própria transação: este código ainda não conta
com uma abstração de unit-of-work compartilhada entre repositórios, e
nenhuma atividade anterior introduziu uma — se isso mudar, este adapter
deve ser revisitado.

Corrida em `create`/`update`: em vez de um SELECT prévio (sujeito a
TOCTOU sob concorrência), o INSERT/UPDATE é tentado diretamente e uma
violação da unique constraint (`tenant_id` + `name`, RAG-011) vira
`KnowledgeBaseNameConflictError` a partir do `IntegrityError` do
Postgres — a mesma constraint que já protege a linha no banco também
garante a consistência aqui.

Não tem teste próprio de integração contra um Postgres real neste
sandbox (mesma limitação documentada em `adapters/postgres/engine.py`,
RAG-006, e em `tests/unit/test_schema.py`, RAG-011): a cobertura de
comportamento fica em `adapters/knowledge_base_repository/in_memory.py`
e nos testes de `packages/application/commands`/`queries`, que exercitam
exatamente o mesmo contrato de `KnowledgeBaseRepositoryPort`.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import literal, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.postgres.models.knowledge_base import KnowledgeBaseModel
from packages.application.ports.knowledge_base_repository import (
    KnowledgeBaseNameConflictError,
    KnowledgeBasePage,
    KnowledgeBaseRepositoryPort,
)
from packages.domain.entities.knowledge_base import KnowledgeBase
from packages.domain.enums.knowledge_base_status import KnowledgeBaseStatus

_MUTABLE_FIELDS = ("name", "description", "config")


def _to_entity(model: KnowledgeBaseModel) -> KnowledgeBase:
    return KnowledgeBase(
        id=model.id,
        tenant_id=model.tenant_id,
        name=model.name,
        description=model.description,
        status=model.status,
        config=model.config,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _encode_cursor(model: KnowledgeBaseModel) -> str:
    return f"{model.created_at.isoformat()}|{model.id}"


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    created_at_raw, _, id_raw = cursor.partition("|")
    return datetime.fromisoformat(created_at_raw), UUID(id_raw)


class PostgresKnowledgeBaseRepository(KnowledgeBaseRepositoryPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, tenant_id: UUID, name: str, description: str | None, config: dict[str, Any]
    ) -> KnowledgeBase:
        now = datetime.now(UTC)
        model = KnowledgeBaseModel(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name,
            description=description,
            status=KnowledgeBaseStatus.ACTIVE,
            config=config,
            created_at=now,
            updated_at=now,
        )
        self._session.add(model)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise KnowledgeBaseNameConflictError(name) from exc
        return _to_entity(model)

    async def _get_model(
        self, *, tenant_id: UUID, knowledge_base_id: UUID
    ) -> KnowledgeBaseModel | None:
        stmt = select(KnowledgeBaseModel).where(
            KnowledgeBaseModel.id == knowledge_base_id,
            KnowledgeBaseModel.tenant_id == tenant_id,
            KnowledgeBaseModel.status == KnowledgeBaseStatus.ACTIVE,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> KnowledgeBase | None:
        model = await self._get_model(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id)
        return _to_entity(model) if model is not None else None

    async def list_by_tenant(
        self, *, tenant_id: UUID, limit: int, cursor: str | None
    ) -> KnowledgeBasePage:
        stmt = (
            select(KnowledgeBaseModel)
            .where(
                KnowledgeBaseModel.tenant_id == tenant_id,
                KnowledgeBaseModel.status == KnowledgeBaseStatus.ACTIVE,
            )
            .order_by(KnowledgeBaseModel.created_at, KnowledgeBaseModel.id)
            .limit(limit + 1)
        )
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor)
            stmt = stmt.where(
                tuple_(KnowledgeBaseModel.created_at, KnowledgeBaseModel.id)
                > tuple_(literal(after_created_at), literal(after_id))
            )
        result = await self._session.execute(stmt)
        models = list(result.scalars().all())
        page_models = models[:limit]
        next_cursor = _encode_cursor(page_models[-1]) if len(models) > limit else None
        items = [_to_entity(m) for m in page_models]
        return KnowledgeBasePage(items=items, next_cursor=next_cursor)

    async def update(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, fields: Mapping[str, Any]
    ) -> KnowledgeBase | None:
        model = await self._get_model(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id)
        if model is None:
            return None
        for key in _MUTABLE_FIELDS:
            if key in fields:
                setattr(model, key, fields[key])
        model.updated_at = datetime.now(UTC)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise KnowledgeBaseNameConflictError(str(fields.get("name"))) from exc
        await self._session.refresh(model)
        return _to_entity(model)

    async def soft_delete(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> bool:
        model = await self._get_model(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id)
        if model is None:
            return False
        model.status = KnowledgeBaseStatus.DELETED
        model.updated_at = datetime.now(UTC)
        await self._session.commit()
        return True
