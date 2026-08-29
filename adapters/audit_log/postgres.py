"""Adapter de `AuditLogPort` via SQLAlchemy/PostgreSQL (RAG-054)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from adapters.postgres.models.audit_event import AuditEventModel
from packages.application.ports.audit_log import AuditEvent, AuditLogPort


def _to_entity(model: AuditEventModel) -> AuditEvent:
    return AuditEvent(
        id=model.id,
        tenant_id=model.tenant_id,
        actor=model.actor,
        action=model.action,
        resource_type=model.resource_type,
        resource_id=model.resource_id,
        occurred_at=model.occurred_at,
    )


class PostgresAuditLogRepository(AuditLogPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        tenant_id: UUID,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: UUID,
    ) -> AuditEvent:
        model = AuditEventModel(
            id=uuid4(),
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=datetime.now(UTC),
        )
        self._session.add(model)
        await self._session.commit()
        return _to_entity(model)
