"""Adapter em memória de `AuditLogPort` (RAG-054) — só para testes.

`events` é público (não parte da porta): é assim que os testes
inspecionam o que foi registrado, mesmo padrão de
`InMemoryLexicalSearch.index_chunk` (RAG-031) e outros fakes deste
projeto — um método/atributo de inspeção que só o fake tem, nunca
promovido à porta abstrata."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from packages.application.ports.audit_log import AuditEvent, AuditLogPort


class InMemoryAuditLog(AuditLogPort):
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def record(
        self,
        *,
        tenant_id: UUID,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: UUID,
    ) -> AuditEvent:
        event = AuditEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=datetime.now(UTC),
        )
        self.events.append(event)
        return event
